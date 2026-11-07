from os import path
from pathlib import Path
from random import randint

from pydantic import (
    BaseModel,
    Field,
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

    rate_limit_window_seconds: PositiveInt = 60
    rate_limit_login: PositiveInt = 10
    rate_limit_register: PositiveInt = 5
    rate_limit_refresh: PositiveInt = 30
    rate_limit_user: PositiveInt = 120

    domain: str | None = None
    cookie_domain: str | None = None

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


settings = Settings()  # type: ignore
