from os import path
from random import randint

from pydantic import Field, field_validator, model_validator
from pydantic_extra_types.dsn import PostgresDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env" if path.exists(".env") else None}

    database_url: PostgresDsn
    jwt_token: str

    domain: str | None = None
    cookie_domain: str | None = None

    bootstrap_username: str | None = None
    bootstrap_password: str | None = None

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
