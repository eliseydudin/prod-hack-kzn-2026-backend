from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, StringConstraints
from sqlmodel import Field

from app.models.base import Base
from app.models.event import Event, LocalDateTime
from app.models.movement import Movement
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


class TransferTicketRequest(Base):
    kind: Literal[TicketKind.PLANE, TicketKind.TRAIN, TicketKind.SHIP]
    departure: TicketPoint
    arrival: TicketPoint
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None


class StayTicketRequest(Base):
    kind: Literal[TicketKind.STAY]
    stay: TicketPoint
    started_at: LocalDateTime
    ended_at: LocalDateTime | None = None


TicketApplyRequest = Annotated[
    TransferTicketRequest | StayTicketRequest, Field(discriminator="kind")
]


class TicketApplyResult(Base):
    points: list[Waypoint]
    transfer: Movement | None = None
    event: Event | None = None
