from os import path
from random import randint
from typing import Optional

from pydantic import Field, field_validator
from pydantic_extra_types.dsn import PostgresDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    class Config:
        env_file = ".env" if path.exists(".env") else None

    database_url: PostgresDsn
    jwt_token: str

    frontend_build_path: Optional[str] = None

    admin_credentials: Optional[str] = None
    base_domain: Optional[str] = None
    cookie_domain: Optional[str] = None

    google_client_id: str
    google_client_secret: str
    google_callback_url: str

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

    @field_validator("admin_credentials", mode="after")
    @classmethod
    def validate_admin_credentials(cls, value: Optional[str]):
        if value is None:
            return value

        username, password = value.split(",")
        assert len(username) > 0
        assert len(password) > 0

        return value

    def unwrap_admin_credentials(self) -> tuple[str, str]:
        if self.admin_credentials is None:
            raise ValueError("admin credentials are missing")
        username, password = self.admin_credentials.split(",")
        return (username, password)


settings = Settings()  # type: ignore
