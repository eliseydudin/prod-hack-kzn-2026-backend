from datetime import timedelta
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Literal, NoReturn

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from app.database import Session
from app.integrations.google_drive.media import Photo
from app.integrations.google_drive.provider import DRIVE_SCOPE, Provider, ProviderError
from app.models.google_drive import (
    DriveConnectionStatus,
    DriveExportStatus,
    GoogleDriveConnect,
    GoogleDriveConnection,
    GoogleDriveConnectionTable,
    GoogleDriveExport,
    GoogleDriveExportTable,
)
from app.models.types.time import utc_now
from app.models.user import UserTable
from app.responses import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.settings import Settings

OAUTH_TTL_SECONDS = 600


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class DriveService:
    def __init__(self, session: Session, provider: Provider, config: Settings):
        self.session = session
        self.provider = provider
        self.config = config
        self.cipher = Fernet(
            config.google_drive_token_encryption_key.get_secret_value().encode()
        )

    async def _lock(
        self, user_id: int, *, create: bool = False
    ) -> GoogleDriveConnectionTable:
        try:
            user = await self.session.scalar(
                select(UserTable)
                .where(UserTable.primary_key == user_id, UserTable.enabled == True)
                .with_for_update(nowait=True)
                .execution_options(populate_existing=True)
            )
        except DBAPIError as error:
            await self.session.rollback()
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise ConflictError(
                    detail={"reason": "drive_operation_in_progress"}
                ) from None
            raise
        if user is None:
            raise AuthorizationError()
        connection = await self.session.scalar(
            select(GoogleDriveConnectionTable)
            .where(GoogleDriveConnectionTable.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if connection is None:
            if not create:
                raise ConflictError(detail={"reason": "drive_not_connected"})
            connection = GoogleDriveConnectionTable(user_id=user_id)
            self.session.add(connection)
            await self.session.flush()
        return connection

    def _decrypt(self, connection: GoogleDriveConnectionTable) -> str:
        try:
            return self.cipher.decrypt(
                connection.refresh_token_encrypted.encode()
            ).decode()
        except InvalidToken, AttributeError, UnicodeError:
            raise ServiceUnavailableError(
                detail={"reason": "drive_encryption_key_unavailable"}
            ) from None

    async def begin(self, user: UserTable) -> tuple[GoogleDriveConnect, str]:
        connection = await self._lock(user.primary_key, create=True)
        if connection.revocation_pending:
            raise ConflictError(detail={"reason": "drive_revocation_pending"})
        state, cookie = token_urlsafe(32), token_urlsafe(32)
        connection.oauth_state_hash = digest(state)
        connection.oauth_cookie_hash = digest(cookie)
        connection.oauth_expires_at = utc_now() + timedelta(seconds=OAUTH_TTL_SECONDS)
        connection.oauth_state_used = False
        self.session.add(connection)
        await self.session.commit()
        return GoogleDriveConnect(
            authorization_url=await self.provider.authorization_url(state)
        ), cookie

    async def callback(
        self, state: str, cookie: str | None, code: str | None, denied: bool
    ) -> Literal["connected", "denied"]:
        if not cookie:
            raise BadRequestError(detail={"reason": "invalid_drive_oauth_state"})
        state_hash = digest(state)
        owner = await self.session.scalar(
            select(GoogleDriveConnectionTable.user_id).where(
                GoogleDriveConnectionTable.oauth_state_hash == state_hash
            )
        )
        if owner is None:
            raise BadRequestError(detail={"reason": "invalid_drive_oauth_state"})
        connection = await self._lock(owner)
        if (
            connection.oauth_state_hash != state_hash
            or connection.oauth_state_used
            or connection.oauth_expires_at is None
            or connection.oauth_expires_at <= utc_now()
            or not compare_digest(connection.oauth_cookie_hash or "", digest(cookie))
        ):
            raise BadRequestError(detail={"reason": "invalid_drive_oauth_state"})
        generation = connection.generation
        connection.oauth_state_used = True
        self.session.add(connection)
        await self.session.commit()
        if denied:
            return "denied"
        if not code:
            raise BadRequestError(detail={"reason": "missing_drive_oauth_code"})
        try:
            token = await self.provider.exchange(code)
            if DRIVE_SCOPE not in token.get("scope", "").split():
                raise BadRequestError(detail={"reason": "drive_scope_not_granted"})
            account = await self.provider.account(token["access_token"])
        except ProviderError as error:
            raise ServiceUnavailableError(detail={"reason": error.reason}) from None
        connection = await self._lock(owner)
        if (
            connection.oauth_state_hash != state_hash
            or connection.generation != generation
        ):
            raise ConflictError(detail={"reason": "drive_connection_changed"})
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            if (
                connection.permission_id != account["permissionId"]
                or not connection.refresh_token_encrypted
                or connection.status != DriveConnectionStatus.CONNECTED
            ):
                raise ConflictError(detail={"reason": "drive_offline_consent_required"})
            refresh_token = self._decrypt(connection)
        if connection.permission_id != account["permissionId"]:
            connection.folder_id = None
        connection.permission_id = account["permissionId"]
        connection.email = account.get("emailAddress")
        connection.refresh_token_encrypted = self.cipher.encrypt(
            refresh_token.encode()
        ).decode()
        connection.scopes = token["scope"]
        connection.status = DriveConnectionStatus.CONNECTED
        connection.revocation_pending = False
        connection.generation += 1
        connection.oauth_state_hash = connection.oauth_cookie_hash = None
        connection.oauth_expires_at = None
        self.session.add(connection)
        await self.session.commit()
        return "connected"

    async def _access(self, connection: GoogleDriveConnectionTable) -> str:
        token = await self.provider.refresh(self._decrypt(connection))
        if token.get("refresh_token"):
            connection.refresh_token_encrypted = self.cipher.encrypt(
                token["refresh_token"].encode()
            ).decode()
            self.session.add(connection)
        if DRIVE_SCOPE not in token.get("scope", DRIVE_SCOPE).split():
            raise ProviderError("drive_scope_not_granted", reconnect=True)
        return token["access_token"]

    async def _failure(
        self,
        connection: GoogleDriveConnectionTable,
        error: ProviderError,
        operation: GoogleDriveExportTable | None = None,
    ) -> NoReturn:
        detail = {"reason": error.reason}
        if error.reconnect:
            connection.status = DriveConnectionStatus.RECONNECT_REQUIRED
            self.session.add(connection)
        if operation is not None:
            operation.status = (
                DriveExportStatus.UNCERTAIN
                if error.retryable
                else DriveExportStatus.FAILED
            )
            operation.error_reason = error.reason
            self.session.add(operation)
            detail["exportId"] = str(operation.id)
        await self.session.commit()
        if error.reconnect:
            raise ConflictError(detail=detail)
        raise ServiceUnavailableError(detail=detail)

    @staticmethod
    def _validate_repeat(
        operation: GoogleDriveExportTable,
        connection: GoogleDriveConnectionTable,
        photo: Photo,
    ) -> None:
        if (
            operation.connection_id != connection.primary_key
            or operation.connection_generation != connection.generation
            or operation.sha256 != photo.sha256
            or operation.name != photo.name
            or operation.mime_type != photo.mime_type
            or operation.size != len(photo.data)
        ):
            raise ConflictError(detail={"reason": "drive_idempotency_conflict"})

    async def export(
        self, user: UserTable, key: str, photo: Photo
    ) -> GoogleDriveExport:
        user_id, user_public_id = user.primary_key, user.id
        connection = await self._lock(user_id)
        if (
            connection.status != DriveConnectionStatus.CONNECTED
            or connection.revocation_pending
        ):
            raise ConflictError(detail={"reason": "drive_reconnect_required"})
        operation = await self.session.scalar(
            select(GoogleDriveExportTable).where(
                GoogleDriveExportTable.user_id == user_id,
                GoogleDriveExportTable.idempotency_key == key,
            )
        )
        if operation is not None:
            self._validate_repeat(operation, connection, photo)
            if operation.status == DriveExportStatus.SUCCEEDED:
                return GoogleDriveExport.model_validate(operation)
        try:
            access = await self._access(connection)
            if operation is None:
                ids = await self.provider.generate_ids(
                    access, 1 if connection.folder_id else 2
                )
                if not connection.folder_id:
                    connection.folder_id = ids.pop()
                operation = GoogleDriveExportTable(
                    user_id=user_id,
                    connection_id=connection.primary_key,
                    connection_generation=connection.generation,
                    idempotency_key=key,
                    sha256=photo.sha256,
                    md5=photo.md5,
                    name=photo.name,
                    mime_type=photo.mime_type,
                    size=len(photo.data),
                    drive_file_id=ids[0],
                )
                self.session.add_all([connection, operation])
        except ProviderError as error:
            await self._failure(connection, error, operation)
        generation = connection.generation
        await self.session.commit()
        connection = await self._lock(user_id)
        if (
            connection.generation != generation
            or connection.status != DriveConnectionStatus.CONNECTED
        ):
            raise ConflictError(detail={"reason": "drive_connection_changed"})
        await self.session.refresh(operation)
        if operation.status == DriveExportStatus.SUCCEEDED:
            return GoogleDriveExport.model_validate(operation)
        try:
            metadata = await self.provider.get_file(access, operation.drive_file_id)
            if metadata is None:
                await self._ensure_folder(access, connection.folder_id, user_public_id)
                try:
                    metadata = await self.provider.upload_photo(
                        access,
                        operation.drive_file_id,
                        connection.folder_id,
                        str(operation.id),
                        photo.name,
                        photo.mime_type,
                        photo.data,
                    )
                except ProviderError as error:
                    if error.status_code != 409:
                        raise
                    metadata = await self.provider.get_file(
                        access, operation.drive_file_id
                    )
                    if metadata is None:
                        raise ProviderError(
                            "drive_result_unavailable", retryable=True
                        ) from None
            self._verify_photo(metadata, operation, connection.folder_id)
        except ProviderError as error:
            await self._failure(connection, error, operation)
        except ConflictError as error:
            operation.status = DriveExportStatus.FAILED
            operation.error_reason = error.detail.get("reason", "drive_file_mismatch")
            self.session.add(operation)
            await self.session.commit()
            raise
        operation.status = DriveExportStatus.SUCCEEDED
        operation.error_reason = None
        operation.web_view_link = (
            f"https://drive.google.com/file/d/{operation.drive_file_id}/view"
        )
        self.session.add(operation)
        await self.session.commit()
        return GoogleDriveExport.model_validate(operation)

    async def _ensure_folder(self, access: str, folder_id: str, owner_id: int) -> None:
        metadata = await self.provider.get_file(access, folder_id)
        if metadata is None:
            try:
                metadata = await self.provider.create_folder(
                    access, folder_id, str(owner_id)
                )
            except ProviderError as error:
                if error.status_code != 409:
                    raise
                metadata = await self.provider.get_file(access, folder_id)
        if (
            not metadata
            or metadata.get("id") != folder_id
            or metadata.get("trashed")
            or metadata.get("mimeType") != "application/vnd.google-apps.folder"
            or metadata.get("appProperties", {}).get("travelhubUserId") != str(owner_id)
        ):
            raise ConflictError(detail={"reason": "drive_folder_unavailable"})

    @staticmethod
    def _verify_photo(
        metadata: dict, operation: GoogleDriveExportTable, folder_id: str
    ) -> None:
        if (
            metadata.get("id") != operation.drive_file_id
            or metadata.get("trashed")
            or metadata.get("mimeType") != operation.mime_type
            or str(metadata.get("size")) != str(operation.size)
            or metadata.get("md5Checksum") != operation.md5
            or folder_id not in metadata.get("parents", [])
            or metadata.get("appProperties", {}).get("travelhubExportId")
            != str(operation.id)
        ):
            raise ConflictError(detail={"reason": "drive_file_mismatch"})

    async def disconnect(self, user: UserTable) -> GoogleDriveConnection:
        user_id = user.primary_key
        connection = await self._lock(user_id, create=True)
        connection.status = DriveConnectionStatus.DISCONNECTED
        connection.folder_id = None
        connection.email = None
        connection.generation += 1
        connection.oauth_state_hash = connection.oauth_cookie_hash = None
        connection.oauth_expires_at = None
        generation = connection.generation
        needs_revoke = bool(connection.refresh_token_encrypted)
        connection.revocation_pending = needs_revoke
        self.session.add(connection)
        await self.session.commit()
        if not needs_revoke:
            return GoogleDriveConnection(enabled=True)

        connection = await self._lock(user_id)
        if connection.generation != generation:
            raise ConflictError(detail={"reason": "drive_connection_changed"})
        try:
            await self.provider.revoke(self._decrypt(connection))
        except ProviderError:
            return GoogleDriveConnection(enabled=True, revocation_pending=True)
        connection.refresh_token_encrypted = None
        connection.revocation_pending = False
        self.session.add(connection)
        await self.session.commit()
        return GoogleDriveConnection(enabled=True)


async def connection_status(
    session: Session, user: UserTable, enabled: bool
) -> GoogleDriveConnection:
    if not enabled:
        return GoogleDriveConnection(
            enabled=False, status=DriveConnectionStatus.DISABLED
        )
    connection = await session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == user.primary_key
        )
    )
    if connection is None:
        return GoogleDriveConnection(enabled=True)
    return GoogleDriveConnection(
        enabled=True,
        status=connection.status,
        email=connection.email,
        revocation_pending=connection.revocation_pending,
    )


async def get_export(
    session: Session, user: UserTable, export_id: int
) -> GoogleDriveExport:
    operation = await session.scalar(
        select(GoogleDriveExportTable).where(
            GoogleDriveExportTable.id == export_id,
            GoogleDriveExportTable.user_id == user.primary_key,
        )
    )
    if operation is None:
        raise NotFoundError()
    return GoogleDriveExport.model_validate(operation)
