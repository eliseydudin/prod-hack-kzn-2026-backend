from functools import cached_property
from os import path
from typing import Annotated, Optional

from pydantic import UrlConstraints, field_validator, model_validator
from pydantic_core import MultiHostUrl
from pydantic_extra_types.dsn import PostgresDsn
from pydantic_settings import BaseSettings

AsyncSqliteDsn = Annotated[
    MultiHostUrl,
    UrlConstraints(
        host_required=False,
        allowed_schemes=[
            "sqlite+aiosqlite",
        ],
    ),
]

DOTENV_EXISTS = path.exists(".env")


class Settings(BaseSettings):
    class Config:
        env_file = ".env" if DOTENV_EXISTS else None

    postgres_dsn: Optional[PostgresDsn] = None
    sqlite_dsn: Optional[AsyncSqliteDsn] = None

    @cached_property
    def database_url(self) -> str:
        if self.postgres_dsn:
            return self.postgres_dsn.unicode_string()
        elif self.sqlite_dsn:
            return self.sqlite_dsn.unicode_string()

        raise RuntimeError("no database url given")

    @field_validator("postgres_dsn", mode="after")
    @classmethod
    def validate_database_url(cls, value: Optional[PostgresDsn]):
        if value and value.scheme != "postgresql+asyncpg":
            raise ValueError("currently only the 'asyncpg' driver is supported")
        return value

    @model_validator(mode="after")
    def verify_database_urls(self):
        if not (self.postgres_dsn or self.sqlite_dsn):
            raise RuntimeError("no database url given")
        return self


settings = Settings()  # type: ignore
