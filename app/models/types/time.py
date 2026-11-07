from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from pydantic import AfterValidator, AwareDatetime, PlainSerializer
from sqlmodel import TIMESTAMP, Column, Field, func


def utc_now() -> datetime:
    return datetime.now(UTC)


def datetime_to_int(dt: datetime) -> int:
    return int(dt.timestamp())


def datetime_to_ms(dt: datetime) -> int:
    if dt is None:
        # used when fastapi is generating the OpenAPI doc
        dt = datetime.now(UTC)
    return int(dt.timestamp() * 1000.0)


def normalize_utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError, ValueError:
        raise ValueError("timestamp is outside the supported UTC range") from None


Timestamp = Annotated[AwareDatetime, AfterValidator(normalize_utc)]
IntTimestamp = Annotated[Timestamp, PlainSerializer(datetime_to_int)]
IntTimestampMs = Annotated[Timestamp, PlainSerializer(datetime_to_ms)]


def UtcDefault(*, delay: Optional[timedelta] = None):
    delay = delay or timedelta()
    return Field(default_factory=lambda: utc_now() + delay)


def CreatedAt(*, exclude: bool = False):
    return Field(
        default=None,
        sa_column=Column(
            TIMESTAMP(timezone=True),
            server_default=func.current_timestamp(),
        ),
        exclude=exclude,
    )


def UpdatedAt(*, exclude: bool = False):
    return Field(
        default=None,
        sa_column=Column(
            TIMESTAMP(timezone=True),
            server_default=func.current_timestamp(),
            onupdate=func.current_timestamp(),
        ),
        exclude=exclude,
    )
