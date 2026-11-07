import hashlib
import json
from io import BytesIO
from logging import LogRecord
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import UploadFile
from PIL import Image
from pydantic import SecretStr
from uvicorn.logging import AccessFormatter

from app.integrations.google_drive.logging import DriveAccessLogFilter
from app.integrations.google_drive.media import read_photo
from app.integrations.google_drive.provider import DRIVE_SCOPE, Provider, ProviderError
from app.responses import BadRequestError, HTTPException


def config():
    return SimpleNamespace(
        google_drive_client_id="test-client",
        google_drive_client_secret=SecretStr("test-client-secret"),
        google_drive_redirect_uri="https://travel.test/api/v1/integrations/google-drive/callback",
    )


def photo_bytes(image_format="PNG"):
    output = BytesIO()
    Image.new("RGB", (3, 2), "blue").save(output, format=image_format)
    return output.getvalue()


async def test_authorization_only_requests_drive_file_and_offline_access():
    async with httpx.AsyncClient() as http:
        provider = Provider(http, config())
        query = parse_qs(
            urlsplit(await provider.authorization_url("random-state")).query
        )
    assert query["scope"] == [DRIVE_SCOPE]
    assert query["state"] == ["random-state"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent select_account"]


async def test_refresh_invalid_grant_requires_reconnect_without_leaking_body():
    def handler(request):
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "secret-provider-body",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError) as caught:
            await Provider(http, config()).refresh("private-refresh-token")
    assert caught.value.reconnect
    assert not caught.value.retryable
    assert str(caught.value) == "google_reconnect_required"
    assert "secret-provider-body" not in repr(caught.value)
    assert "private-refresh-token" not in repr(caught.value)


async def test_network_failure_is_retryable_and_never_retried_automatically():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError) as caught:
            await Provider(http, config()).refresh("private-refresh-token")
    assert caught.value.retryable
    assert len(calls) == 1
    assert str(caught.value) == "google_unavailable"


async def test_revoking_an_already_invalid_token_is_idempotent():
    def handler(request):
        assert str(request.url) == "https://oauth2.googleapis.com/revoke"
        return httpx.Response(400, json={"error": "invalid_token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await Provider(http, config()).revoke("already-revoked-token")


async def test_account_identifier_does_not_depend_on_email_scope():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"user": {"permissionId": "123"}})
        )
    ) as http:
        assert await Provider(http, config()).account("access-token") == {
            "permissionId": "123"
        }


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": []},
        {"scope": None},
        {"refresh_token": {}},
        {"refresh_token": ""},
        {"access_token": "bad\r\ntoken"},
        {"scope": DRIVE_SCOPE + " \x00"},
        {"scope": DRIVE_SCOPE + " \ud800"},
    ],
)
async def test_malformed_successful_oauth_response_is_a_safe_provider_error(changes):
    token = {"access_token": "valid-access", "scope": DRIVE_SCOPE, **changes}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps(token).encode())
        )
    ) as http:
        with pytest.raises(ProviderError, match="google_invalid_response"):
            await Provider(http, config()).exchange("code")


async def test_oauth_token_must_be_a_json_object_before_library_coercion():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=[["access_token", "valid-access"], ["scope", DRIVE_SCOPE]]
            )
        )
    ) as http:
        with pytest.raises(ProviderError, match="google_invalid_response"):
            await Provider(http, config()).exchange("code")


@pytest.mark.parametrize(
    "user",
    [
        {"permissionId": "x" * 256},
        {"permissionId": "valid", "emailAddress": []},
        {"permissionId": "valid", "emailAddress": "x" * 321},
        {"permissionId": "valid", "emailAddress": "bad\x00@example.test"},
        {"permissionId": "valid", "emailAddress": "bad\ud800@example.test"},
    ],
)
async def test_invalid_google_account_metadata_never_reaches_the_database(user):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps({"user": user}).encode())
        )
    ) as http:
        with pytest.raises(ProviderError, match="google_invalid_response"):
            await Provider(http, config()).account("access")


@pytest.mark.parametrize(
    "changes", [{"appProperties": None}, {"parents": "folder"}, {"trashed": "false"}]
)
async def test_invalid_file_metadata_is_a_safe_provider_error(changes):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": "file", **changes})
        )
    ) as http:
        with pytest.raises(ProviderError, match="google_invalid_response"):
            await Provider(http, config()).get_file("access", "file")


def test_uvicorn_access_log_does_not_contain_oauth_code_or_state():
    record = LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "GET",
            "/api/v1/integrations/google-drive/callback?code=private-code&state=private-state",
            "1.1",
            303,
        ),
        None,
    )
    assert DriveAccessLogFilter().filter(record)
    assert "callback HTTP" in record.getMessage()
    assert "private-" not in record.getMessage()
    assert "private-" not in AccessFormatter(use_colors=False).format(record)


async def test_resumable_upload_sends_bytes_and_stable_file_id():
    calls = []
    data = photo_bytes()

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer access-token"
        if request.method == "POST":
            metadata = json.loads(request.content)
            assert metadata["id"] == "reserved-file"
            assert metadata["parents"] == ["folder"]
            assert metadata["appProperties"] == {"travelhubExportId": "export123"}
            assert request.headers["X-Upload-Content-Length"] == str(len(data))
            return httpx.Response(
                200,
                headers={
                    "Location": "https://www.googleapis.com/upload/drive/v3/files?upload_id=opaque"
                },
            )
        assert request.method == "PUT"
        assert request.content == data
        assert request.headers["Content-Type"] == "image/png"
        return httpx.Response(200, json={"id": "reserved-file", "size": str(len(data))})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await Provider(http, config()).upload_photo(
            "access-token",
            "reserved-file",
            "folder",
            "export123",
            "photo.png",
            "image/png",
            data,
        )
    assert result["id"] == "reserved-file"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.test/upload/drive/v3/files?upload_id=secret",
        "https://www.googleapis.com@evil.test/upload/drive/v3/files?upload_id=secret",
        "http://www.googleapis.com/upload/drive/v3/files?upload_id=secret",
        "https://www.googleapis.com/drive/v3/files?upload_id=secret",
        "https://[invalid-host/upload/drive/v3/files?upload_id=secret",
    ],
)
async def test_upload_rejects_untrusted_session_location_without_forwarding_token(
    location,
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"Location": location})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match="google_invalid_upload_location"):
            await Provider(http, config()).upload_photo(
                "secret-access-token",
                "file",
                "folder",
                "operation",
                "photo.png",
                "image/png",
                b"data",
            )
    assert len(calls) == 1


async def test_conflicting_create_is_left_for_service_reconciliation():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(409, json={"error": {"code": 409}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError) as caught:
            await Provider(http, config()).create_folder(
                "access-token", "folder", "owner"
            )
    assert caught.value.status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize(
    "image_format,mime,extension",
    [("JPEG", "image/jpeg", ".jpg"), ("PNG", "image/png", ".png")],
)
async def test_media_uses_real_format_and_preserves_photo_bytes(
    image_format, mime, extension
):
    data = photo_bytes(image_format)
    upload = UploadFile(BytesIO(data), filename="../../private\\actual\x00name.exe")
    photo = await read_photo(upload, max_bytes=len(data))
    assert photo.data == data
    assert photo.mime_type == mime
    assert photo.name == "actualname" + extension
    assert photo.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "data,max_bytes,reason",
    [(b"<html>fake.png</html>", 100, "photo_invalid"), (b"huge", 3, "photo_too_large")],
)
async def test_media_rejects_invalid_or_oversized_files(data, max_bytes, reason):
    with pytest.raises(HTTPException) as caught:
        await read_photo(UploadFile(BytesIO(data), filename="fake.png"), max_bytes)
    assert caught.value.detail["reason"] == reason
    assert caught.value.status_code == (413 if reason == "photo_too_large" else 400)


async def test_truncated_jpeg_is_rejected_after_header_detection():
    data = photo_bytes("JPEG")[:-10]
    with pytest.raises(BadRequestError) as caught:
        await read_photo(UploadFile(BytesIO(data), filename="photo.jpg"), 1000)
    assert caught.value.detail["reason"] == "photo_invalid"
