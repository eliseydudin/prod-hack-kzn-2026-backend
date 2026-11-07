from datetime import datetime
from enum import StrEnum

from sqlalchemy import UniqueConstraint
from sqlmodel import Column, DateTime, Field, String, Text

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.types.time import CreatedAt, Timestamp


class DriveConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    RECONNECT_REQUIRED = "reconnect_required"
    DISABLED = "disabled"


class DriveExportStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


class GoogleDriveConnectionBase(Base):
    status: DriveConnectionStatus = Field(
        default=DriveConnectionStatus.DISCONNECTED, sa_type=String(24)
    )
    email: str | None = Field(default=None, sa_type=String(320))
    revocation_pending: bool = False


class GoogleDriveConnectionTable(GoogleDriveConnectionBase, PrimaryKeyBase, table=True):
    user_id: int = Field(foreign_key="user.primary_key", unique=True)
    generation: int = 0
    permission_id: str | None = Field(default=None, sa_type=String(255))
    refresh_token_encrypted: str | None = Field(default=None, sa_type=Text)
    scopes: str | None = Field(default=None, sa_type=Text)
    folder_id: str | None = Field(default=None, sa_type=String(255))
    oauth_state_hash: str | None = Field(default=None, sa_type=String(64), unique=True)
    oauth_cookie_hash: str | None = Field(default=None, sa_type=String(64))
    oauth_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    oauth_state_used: bool = False


class GoogleDriveConnection(GoogleDriveConnectionBase):
    enabled: bool


class GoogleDriveConnect(Base):
    authorization_url: str


class GoogleDriveExportBase(SnowflakeIdBase):
    name: str = Field(sa_type=String(128))
    mime_type: str = Field(sa_type=String(32))
    size: int
    status: DriveExportStatus = Field(
        default=DriveExportStatus.PENDING, sa_type=String(16)
    )
    drive_file_id: str = Field(sa_type=String(255))
    web_view_link: str | None = Field(default=None, sa_type=Text)
    error_reason: str | None = Field(default=None, sa_type=String(64))
    created_at: Timestamp = CreatedAt()


class GoogleDriveExportTable(GoogleDriveExportBase, PrimaryKeyBase, table=True):
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    user_id: int = Field(foreign_key="user.primary_key")
    connection_id: int = Field(foreign_key="googledriveconnection.primary_key")
    connection_generation: int
    idempotency_key: str = Field(sa_type=String(128))
    sha256: str = Field(sa_type=String(64))
    md5: str = Field(sa_type=String(32))


class GoogleDriveExport(GoogleDriveExportBase):
    pass
