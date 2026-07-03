from datetime import timedelta
from typing import Literal
from uuid import UUID

from sqlmodel import Field

from app.models.base import Base
from app.models.types.extra import Extra
from app.models.types.time import IntTimestamp, UtcDefault
from app.models.user import UserRole

JWT_TOKEN_PATTERN = r"[a-zA-Z0-9]*.[a-zA-Z0-9]*.[a-zA-Z0-9]*"


class AuthToken(Base):
    sub: UUID
    role: UserRole
    iat: IntTimestamp = UtcDefault()
    exp: IntTimestamp = UtcDefault(delay=timedelta(minutes=30))


class RefreshToken(Base):
    sub: UUID
    exp: IntTimestamp = UtcDefault(delay=timedelta(days=7))


class AuthTokenResponse(Base):
    model_config = {"alias_generator": None}

    access_token: str = Field(schema_extra=Extra(pattern=JWT_TOKEN_PATTERN))
    token_type: Literal["bearer"] = "bearer"


class RefreshTokenResponse(Base):
    refresh_token: str
