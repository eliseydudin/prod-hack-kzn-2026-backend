from os import path
from random import randint

from pydantic import Field, model_validator
from pydantic_core import MultiHostUrl
from pydantic_extra_types.dsn import PostgresDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env" if path.exists(".env") else None}

    jwt_token: str

    domain: str | None = None
    cookie_domain: str | None = None

    bootstrap_username: str | None = None
    bootstrap_password: str | None = None

    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str

    machine_id: int = Field(
        default_factory=lambda: randint(0, 2**10 - 1),
        ge=0,
        le=(2**10 - 1),
    )

    @property
    def database_url(self) -> PostgresDsn:
        return MultiHostUrl.build(
            scheme="postgresql+asyncpg",
            host=self.postgres_host,
            username=self.postgres_user,
            password=self.postgres_password,
            path=self.postgres_db,
            port=5432,
        )

    @model_validator(mode="after")
    def validate_bootstrap_credentials(self):
        if (self.bootstrap_password is None) != (self.bootstrap_username is None):
            raise ValueError("invalid bootstrap admin credentials")

        return self


settings = Settings()  # type: ignore
