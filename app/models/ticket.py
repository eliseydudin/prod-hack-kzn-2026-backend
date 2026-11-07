from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import StringConstraints, field_validator, model_validator
from sqlmodel import Field

from app.models.base import Base
from app.models.event import Event
from app.models.movement import Movement
from app.models.types.time import Timestamp
from app.models.waypoint import Waypoint, WaypointLocation


class TicketKind(StrEnum):
    PLANE = "plane"
    TRAIN = "train"
    SHIP = "ship"
    STAY = "stay"


TicketPlaceText = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[^\x00\ud800-\udfff]+$")
]


class TicketPoint(WaypointLocation):
    name: TicketPlaceText = Field(min_length=1, max_length=128)
    address: TicketPlaceText = Field(min_length=1, max_length=512)


class TicketPlaceMatch(Base):
    name: str
    address: str | None = None
    iata: str | None = None
    point: TicketPoint | None = None


class TicketPreview(Base):
    kind: TicketKind
    departure: TicketPlaceMatch | None = None
    arrival: TicketPlaceMatch | None = None
    stay: TicketPlaceMatch | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class TicketTimes(Base):
    started_at: Timestamp
    ended_at: Timestamp | None = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def validate_utc_range(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            try:
                value.astimezone(UTC)
            except OverflowError as error:
                raise ValueError("timestamp is out of range in UTC") from error
        return value

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("endedAt must be on or after startedAt")
        return self


class TransferTicketRequest(TicketTimes):
    kind: Literal[TicketKind.PLANE, TicketKind.TRAIN, TicketKind.SHIP]
    departure: TicketPoint
    arrival: TicketPoint


class StayTicketRequest(TicketTimes):
    kind: Literal[TicketKind.STAY]
    stay: TicketPoint


TicketApplyRequest = Annotated[
    TransferTicketRequest | StayTicketRequest, Field(discriminator="kind")
]


class TicketApplyResult(Base):
    points: list[Waypoint]
    transfer: Movement | None = None
    event: Event | None = None
