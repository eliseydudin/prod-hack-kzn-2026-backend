from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from re import fullmatch
from typing import Any
from urllib.parse import urlsplit

import httpx
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.oauth2 import OAuth2Error, OAuth2RequestError

from app.settings import Settings

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_URL = "https://www.googleapis.com/drive/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FILE_FIELDS = "id,mimeType,size,md5Checksum,parents,trashed,appProperties,webViewLink"


class ProviderError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        retryable: bool = False,
        reconnect: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.reconnect = reconnect
        self.status_code = status_code


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        result = response.json()
    except ValueError:
        raise ProviderError("google_invalid_response", retryable=True) from None
    if not isinstance(result, dict):
        raise ProviderError("google_invalid_response", retryable=True)
    return result


def _error(response: httpx.Response | None) -> ProviderError:
    if response is None:
        return ProviderError("google_unavailable", retryable=True)
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if error == "invalid_grant" or status == 401:
        return ProviderError(
            "google_reconnect_required", reconnect=True, status_code=status
        )
    reasons = set()
    if isinstance(error, dict) and isinstance(error.get("errors"), list):
        reasons = {
            item.get("reason")
            for item in error.get("errors", [])
            if isinstance(item, dict) and isinstance(item.get("reason"), str)
        }
    if status == 429 or reasons.intersection(
        {"rateLimitExceeded", "userRateLimitExceeded", "sharingRateLimitExceeded"}
    ):
        return ProviderError("google_rate_limited", retryable=True, status_code=status)
    if status >= 500 or status in {408, 308}:
        return ProviderError("google_unavailable", retryable=True, status_code=status)
    if status == 403:
        return ProviderError("google_permission_denied", status_code=status)
    return ProviderError("google_request_rejected", status_code=status)


class _SharedGoogleOAuth(GoogleOAuth2):
    def __init__(self, http: httpx.AsyncClient, client_id: str, client_secret: str):
        super().__init__(client_id, client_secret, scopes=[DRIVE_SCOPE])
        self.revoke_token_endpoint = "https://oauth2.googleapis.com/revoke"
        self.http = http

    @asynccontextmanager
    async def get_httpx_client(self) -> AsyncIterator[httpx.AsyncClient]:
        yield self.http

    async def send_request(
        self,
        client: httpx.AsyncClient,
        request: httpx.Request,
        auth: httpx.Auth | None,
        *,
        exc_class: type[OAuth2RequestError],
    ) -> httpx.Response:
        try:
            response = await client.send(request, auth=auth, follow_redirects=False)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise exc_class("Google OAuth request failed", exc.response) from None
        except httpx.HTTPError:
            raise exc_class("Google OAuth unavailable") from None
        return response

    def get_json(
        self, response: httpx.Response, *, exc_class: type[OAuth2RequestError]
    ) -> dict[str, Any]:
        return _json(response)


class Provider:
    def __init__(self, http: httpx.AsyncClient, config: Settings):
        self.http = http
        self.redirect_uri = str(config.google_drive_redirect_uri)
        self.oauth = _SharedGoogleOAuth(
            http,
            config.google_drive_client_id or "",
            config.google_drive_client_secret.get_secret_value()
            if config.google_drive_client_secret
            else "",
        )

    async def authorization_url(self, state: str) -> str:
        return await self.oauth.get_authorization_url(
            self.redirect_uri,
            state=state,
            extras_params={
                "access_type": "offline",
                "prompt": "consent select_account",
            },
        )

    async def exchange(self, code: str) -> dict[str, Any]:
        try:
            token = await self.oauth.get_access_token(code, self.redirect_uri)
        except OAuth2Error as exc:
            raise _error(getattr(exc, "response", None)) from None
        except ValueError, TypeError, KeyError, OverflowError:
            raise ProviderError("google_invalid_response", retryable=True) from None
        return self._token(token)

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        try:
            token = await self.oauth.refresh_token(refresh_token)
        except OAuth2Error as exc:
            raise _error(getattr(exc, "response", None)) from None
        except ValueError, TypeError, KeyError, OverflowError:
            raise ProviderError("google_invalid_response", retryable=True) from None
        return self._token(token)

    @staticmethod
    def _token(token: dict[str, Any]) -> dict[str, Any]:
        result = dict(token)
        for field in ("access_token", "refresh_token"):
            value = result.get(field)
            if field == "refresh_token" and field not in result:
                continue
            if not isinstance(value, str) or not fullmatch(r"[!-~]{1,16384}", value):
                raise ProviderError("google_invalid_response", retryable=True)
        if (
            not isinstance(result.get("token_type", "bearer"), str)
            or result.get("token_type", "bearer").lower() != "bearer"
        ):
            raise ProviderError("google_invalid_response", retryable=True)
        if "scope" not in result:
            result["scope"] = DRIVE_SCOPE
        if not isinstance(result["scope"], str) or not fullmatch(
            r"[\x20-\x7e]{0,16384}", result["scope"]
        ):
            raise ProviderError("google_invalid_response", retryable=True)
        return result

    @staticmethod
    def _file(response: httpx.Response) -> dict[str, Any]:
        metadata = _json(response)
        file_id = metadata.get("id")
        if not isinstance(file_id, str) or not fullmatch(
            r"[A-Za-z0-9_-]{1,255}", file_id
        ):
            raise ProviderError("google_invalid_response", retryable=True)
        if "mimeType" in metadata and not isinstance(metadata["mimeType"], str):
            raise ProviderError("google_invalid_response", retryable=True)
        properties = metadata.get("appProperties", {})
        parents = metadata.get("parents", [])
        if (
            not isinstance(properties, dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in properties.items()
            )
            or not isinstance(parents, list)
            or any(not isinstance(parent, str) for parent in parents)
            or ("trashed" in metadata and not isinstance(metadata["trashed"], bool))
        ):
            raise ProviderError("google_invalid_response", retryable=True)
        return metadata

    async def revoke(self, refresh_token: str) -> None:
        try:
            await self.oauth.revoke_token(refresh_token)
        except OAuth2Error as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 400:
                try:
                    if response.json().get("error") == "invalid_token":
                        return
                except ValueError, AttributeError:
                    pass
            raise _error(response) from None

    async def _request(
        self, method: str, url: str, access_token: str, **kwargs: Any
    ) -> httpx.Response:
        headers = {
            **kwargs.pop("headers", {}),
            "Authorization": f"Bearer {access_token}",
        }
        try:
            response = await self.http.request(
                method, url, headers=headers, follow_redirects=False, **kwargs
            )
        except httpx.HTTPError:
            raise ProviderError("google_unavailable", retryable=True) from None
        if not 200 <= response.status_code < 300:
            raise _error(response)
        return response

    async def account(self, access_token: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"{DRIVE_URL}/about",
            access_token,
            params={"fields": "user(permissionId,emailAddress,displayName)"},
        )
        user = _json(response).get("user")
        if (
            not isinstance(user, dict)
            or not isinstance(user.get("permissionId"), str)
            or not fullmatch(r"[A-Za-z0-9_-]{1,255}", user["permissionId"])
            or (
                user.get("emailAddress") is not None
                and (
                    not isinstance(user["emailAddress"], str)
                    or len(user["emailAddress"]) > 320
                    or not user["emailAddress"].isprintable()
                )
            )
        ):
            raise ProviderError("google_invalid_response", retryable=True)
        return user

    async def generate_ids(self, access_token: str, count: int) -> list[str]:
        response = await self._request(
            "GET",
            f"{DRIVE_URL}/files/generateIds",
            access_token,
            params={"count": count, "space": "drive", "type": "files"},
        )
        ids = _json(response).get("ids")
        if (
            not isinstance(ids, list)
            or len(ids) != count
            or any(
                not isinstance(item, str)
                or not fullmatch(r"[A-Za-z0-9_-]{1,255}", item)
                for item in ids
            )
            or len(set(ids)) != count
        ):
            raise ProviderError("google_invalid_response", retryable=True)
        return ids

    async def get_file(self, access_token: str, file_id: str) -> dict[str, Any] | None:
        try:
            response = await self._request(
                "GET",
                f"{DRIVE_URL}/files/{file_id}",
                access_token,
                params={"fields": FILE_FIELDS},
            )
        except ProviderError as exc:
            if exc.status_code == 404:
                return None
            raise
        return self._file(response)

    async def create_folder(
        self, access_token: str, file_id: str, owner_public_id: str
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"{DRIVE_URL}/files",
            access_token,
            params={"fields": FILE_FIELDS},
            json={
                "id": file_id,
                "name": "TravelHub",
                "mimeType": "application/vnd.google-apps.folder",
                "appProperties": {"travelhubUserId": str(owner_public_id)},
            },
        )
        return self._file(response)

    async def upload_photo(
        self,
        access_token: str,
        file_id: str,
        folder_id: str,
        export_id: str,
        name: str,
        mime_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            UPLOAD_URL,
            access_token,
            params={"uploadType": "resumable", "fields": FILE_FIELDS},
            headers={
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(len(data)),
            },
            json={
                "id": file_id,
                "name": name,
                "mimeType": mime_type,
                "parents": [folder_id],
                "appProperties": {"travelhubExportId": str(export_id)},
            },
        )
        location = response.headers.get("Location", "")
        try:
            parsed = urlsplit(location)
        except ValueError:
            raise ProviderError("google_invalid_upload_location") from None
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.googleapis.com"
            or parsed.path != "/upload/drive/v3/files"
            or not parsed.query
            or parsed.fragment
        ):
            raise ProviderError("google_invalid_upload_location")
        response = await self._request(
            "PUT",
            location,
            access_token,
            headers={"Content-Type": mime_type, "Content-Length": str(len(data))},
            content=data,
        )
        return self._file(response)
