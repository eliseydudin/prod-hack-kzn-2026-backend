from os import path
from pathlib import Path
from random import randint

from cryptography.fernet import Fernet
from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    PositiveInt,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_extra_types.dsn import PostgresDsn
from pydantic_settings import BaseSettings


class StorageSettings(BaseModel):
    endpoint: str = Field(min_length=1)
    access_key: SecretStr = Field(min_length=1)
    secret_key: SecretStr = Field(min_length=1)
    bucket: str = "travelhub"
    secure: bool = True


class Settings(BaseSettings):
    model_config = {
        "env_file": ".env" if path.exists(".env") else None,
        "env_nested_delimiter": "__",
        "hide_input_in_errors": True,
    }

    database_url: PostgresDsn
    jwt_token: str
    redis_url: RedisDsn | None = None
    storage: StorageSettings | None = None
    ticket_geocoder_url: HttpUrl = HttpUrl("https://photon.komoot.io")

    rate_limit_window_seconds: PositiveInt = 60
    rate_limit_login: PositiveInt = 10
    rate_limit_register: PositiveInt = 5
    rate_limit_refresh: PositiveInt = 30
    rate_limit_user: PositiveInt = 120

    domain: str | None = None
    cookie_domain: str | None = None

    google_drive_enabled: bool = False
    google_drive_client_id: str | None = None
    google_drive_client_secret: SecretStr | None = None
    google_drive_redirect_uri: HttpUrl | None = None
    google_drive_token_encryption_key: SecretStr | None = None
    google_drive_return_path: str = "/dashboard"
    google_drive_max_photo_bytes: int = Field(default=10_000_000, ge=1, le=20_000_000)

    bootstrap_username: str | None = None
    bootstrap_password: str | None = None
    push_vapid_key_file: Path | None = Field(
        default=None, validation_alias="PUSH__VAPID_KEY_FILE"
    )
    push_vapid_subject: str | None = Field(
        default=None, validation_alias="PUSH__VAPID_SUBJECT"
    )

    machine_id: int = Field(
        default_factory=lambda: randint(0, 2**10 - 1),
        ge=0,
        le=(2**10 - 1),
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def validate_database_url(cls, value: PostgresDsn):
        if value and value.scheme != "postgresql+asyncpg":
            raise ValueError("currently only the 'asyncpg' driver is supported")
        return value

    @model_validator(mode="after")
    def validate_bootstrap_credentials(self):
        if (self.bootstrap_password is None) != (self.bootstrap_username is None):
            raise ValueError("invalid bootstrap admin credentials")

        return self

    @model_validator(mode="after")
    def validate_google_drive(self):
        if not self.google_drive_enabled:
            return self
        if not all(
            (
                self.google_drive_client_id,
                self.google_drive_client_secret,
                self.google_drive_redirect_uri,
                self.google_drive_token_encryption_key,
            )
        ):
            raise ValueError(
                "Google Drive requires OAuth credentials, redirect URI and encryption key"
            )
        uri = self.google_drive_redirect_uri
        if uri.scheme != "https" and uri.host not in (
            "localhost",
            "127.0.0.1",
            "[::1]",
        ):
            raise ValueError(
                "Google Drive redirect URI requires HTTPS outside localhost"
            )
        if uri.username or uri.password or uri.query or uri.fragment:
            raise ValueError("Google Drive redirect URI must be a plain callback URL")
        callback = "/api/v1/integrations/google-drive/callback"
        if uri.path not in (callback, callback.removeprefix("/api/v1")):
            raise ValueError(
                "Google Drive redirect URI must point to its callback route"
            )
        if (
            not self.google_drive_return_path.startswith("/")
            or self.google_drive_return_path.startswith("//")
            or any(
                c
                not in "/-_.abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                for c in self.google_drive_return_path
            )
        ):
            raise ValueError("Google Drive return path must be a local absolute path")
        try:
            Fernet(self.google_drive_token_encryption_key.get_secret_value().encode())
        except ValueError, TypeError:
            raise ValueError(
                "Google Drive encryption key must be a Fernet key"
            ) from None
        return self


settings = Settings()  # type: ignore
