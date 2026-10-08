from datetime import timedelta
from typing import Annotated, Literal

from pydantic import PlainSerializer
from sqlmodel import Field

from app.models.base import Base
from app.models.types.extra import Extra
from app.models.types.snowflake_id import SnowflakeId
from app.models.types.time import IntTimestamp, UtcDefault
from app.models.user import UserRole

JWT_TOKEN_PATTERN = r"[a-zA-Z0-9]*.[a-zA-Z0-9]*.[a-zA-Z0-9]*"

SnowflakeIdStr = Annotated[SnowflakeId, PlainSerializer(str)]


class AuthToken(Base):
    sub: SnowflakeIdStr
    role: UserRole
    iat: IntTimestamp = UtcDefault()
    exp: IntTimestamp = UtcDefault(delay=timedelta(minutes=30))


class RefreshToken(Base):
    sub: SnowflakeIdStr
    exp: IntTimestamp = UtcDefault(delay=timedelta(days=7))


class AuthTokenResponse(Base):
    class Config:
        alias_generator = None

    access_token: str = Field(schema_extra=Extra(pattern=JWT_TOKEN_PATTERN))
    token_type: Literal["bearer"] = "bearer"


class RefreshTokenResponse(Base):
    refresh_token: str
