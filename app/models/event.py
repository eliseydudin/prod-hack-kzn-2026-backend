from datetime import datetime
from enum import StrEnum, auto
from typing import Annotated

from pydantic import (
    AfterValidator,
    HttpUrl,
    NaiveDatetime,
    StringConstraints,
    TypeAdapter,
    model_validator,
)
from sqlmodel import Field, Relationship, String, Text

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.types.missing import MaybeMissing
from app.models.types.snowflake_id import SnowflakeId
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


DESCRIPTION_LIMIT = 512
EventTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
EventDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_LIMIT)
]
BookingUrl = Annotated[str, AfterValidator(validate_booking_url)]
LocalDateTime = Annotated[datetime, NaiveDatetime]
EventWaypointId = Annotated[SnowflakeId, Field(gt=0)]


class EventBase(Base):
    title: EventTitle = Field(sa_type=String(128))
    type: EventType = Field(sa_type=String(16))
    description: EventDescription | None = Field(
        default=None, sa_type=String(DESCRIPTION_LIMIT)
    )
    started_at: LocalDateTime
    ended_at: LocalDateTime | None = None
    booking_url: BookingUrl | None = Field(default=None, sa_type=Text)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("endedAt must be on or after startedAt")
        return self


class EventExtraBase(EventBase, SnowflakeIdBase):
    pass


class EventTable(EventExtraBase, PrimaryKeyBase, table=True):
    trip_id: int = Field(foreign_key="trip.primary_key")
    waypoint_primary_key: int | None = Field(
        default=None, foreign_key="waypoint.primary_key", ondelete="SET NULL"
    )
    waypoint: WaypointTable | None = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    @property
    def waypoint_id(self) -> int | None:
        return self.waypoint.id if self.waypoint is not None else None


class Event(EventExtraBase):
    waypoint_id: EventWaypointId | None = None


class CreateEventRequest(EventBase):
    waypoint_id: EventWaypointId | None = None


class UpdateEventRequest(Base):
    title: MaybeMissing[EventTitle]
    type: MaybeMissing[EventType]
    description: MaybeMissing[EventDescription | None]
    started_at: MaybeMissing[LocalDateTime]
    ended_at: MaybeMissing[LocalDateTime | None]
    booking_url: MaybeMissing[BookingUrl | None]
    waypoint_id: MaybeMissing[EventWaypointId | None]
