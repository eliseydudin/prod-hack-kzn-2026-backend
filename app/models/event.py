from enum import StrEnum, auto
from typing import Annotated

from pydantic import (
    AfterValidator,
    HttpUrl,
    TypeAdapter,
    model_validator,
)
from pydantic_core import MISSING
from sqlmodel import Field, Relationship, Text

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.trip import TripTable
from app.models.types.missing import MaybeMissing
from app.models.types.snowflake_id import SnowflakeId
from app.models.types.time import Timestamp
from app.models.waypoint import WaypointTable


class EventType(StrEnum):
    WALK = auto()
    TOUR = auto()
    STAY = auto()
    RESTAURANT = auto()
    MEETING = auto()
    OTHER = auto()


_booking_url = TypeAdapter(HttpUrl)


def validate_booking_url(value: str) -> str:
    return str(_booking_url.validate_python(value))


BookingUrl = Annotated[str, AfterValidator(validate_booking_url)]


class EventBase(Base):
    title: str = Field(min_length=1, max_length=128)
    type: EventType
    description: str | None = Field(default=None, min_length=1, max_length=512)
    started_at: Timestamp
    ended_at: Timestamp | None = None
    booking_url: BookingUrl | None = Field(default=None, sa_type=Text)


class EventExtraBase(EventBase, SnowflakeIdBase):
    pass


class EventTable(EventExtraBase, PrimaryKeyBase, table=True):
    trip_id: int | None = Field(
        default=None,
        foreign_key="trip.primary_key",
        nullable=False,
    )
    waypoint_primary_key: int | None = Field(
        default=None,
        foreign_key="waypoint.primary_key",
        ondelete="SET NULL",
    )
    trip: TripTable | None = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    waypoint: WaypointTable | None = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    @property
    def waypoint_id(self) -> SnowflakeId | None:
        return self.waypoint.id if self.waypoint is not None else None


class Event(EventExtraBase):
    waypoint_id: SnowflakeId | None = None


class CreateEventRequest(EventBase):
    waypoint_id: SnowflakeId | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("endedAt must be on or after startedAt")
        return self


class UpdateEventRequest(Base):
    title: MaybeMissing[str] = MISSING
    type: MaybeMissing[EventType] = MISSING
    description: MaybeMissing[str | None] = MISSING
    started_at: MaybeMissing[Timestamp] = MISSING
    ended_at: MaybeMissing[Timestamp | None] = MISSING
    booking_url: MaybeMissing[BookingUrl | None] = MISSING
    waypoint_id: MaybeMissing[SnowflakeId | None] = MISSING

    @model_validator(mode="after")
    def validate_time_range(self):
        if (
            self.ended_at is not MISSING
            and self.started_at is not MISSING
            and self.ended_at is not None
            and self.ended_at < self.started_at
        ):
            raise ValueError("endedAt must be on or after startedAt")
        return self
