from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)
from sqlmodel import Field

from app.models.base import Base
from app.models.event import Event
from app.models.movement import Movement
from app.models.types.time import Timestamp
from app.models.waypoint import Waypoint, WaypointLocation


class TicketKind(StrEnum):
    """plane — авиабилет, train — поезд, ship — судно, stay — гостиница."""

    PLANE = "plane"
    TRAIN = "train"
    SHIP = "ship"
    STAY = "stay"


TicketPlaceText = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[^\x00\ud800-\udfff]+$")
]


class TicketPoint(WaypointLocation):
    """Подтверждённая точка: latitude — широта [-90, 90], longitude — долгота [-180, 180]."""

    name: TicketPlaceText = Field(
        min_length=1,
        max_length=128,
        description="Название аэропорта, вокзала, причала или гостиницы.",
    )
    address: TicketPlaceText = Field(
        min_length=1,
        max_length=512,
        description="Подтверждённый адрес, который сохранится в описании точки.",
    )


class TicketPlaceMatch(Base):
    """Распознанное место. Для apply используйте вложенный point; null требует выбора координат."""

    name: str
    address: str | None = None
    iata: str | None = None
    point: TicketPoint | None = None


class TicketTimeHint(Base):
    """Локальное время из документа, когда ещё нужно подтвердить дату или часовой пояс."""

    month: int
    day: int
    local_time: time
    local_date: date | None = None
    time_zone: str | None = None


class TicketIssue(Base):
    """Поле и причина, мешающие автоматическому подтверждению; исправьте их перед apply."""

    field: Literal["departure", "arrival", "stay", "startedAt", "endedAt"]
    code: Literal[
        "point_required",
        "start_required",
        "date_required",
        "timezone_required",
        "outside_trip",
        "ended_before_start",
    ]
    msg: str


class TicketPreview(Base):
    """Результат разбора без сохранения. Неполные данные возвращаются с issues и HTTP 200."""

    kind: TicketKind
    departure: TicketPlaceMatch | None = None
    arrival: TicketPlaceMatch | None = None
    stay: TicketPlaceMatch | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    started_at_hint: TicketTimeHint | None = None
    ended_at_hint: TicketTimeHint | None = None
    issues: list[TicketIssue] = Field(default_factory=list)

    @computed_field(
        description="true, если распознаватель не нашёл полей, требующих уточнения. Перед сохранением пользователь подтверждает данные."
    )
    @property
    def ready_to_apply(self) -> bool:
        return not self.issues


class TicketTimes(Base):
    started_at: Timestamp = Field(
        description="Время отправления/заселения с UTC-смещением. Например, 2026-12-10T15:00:00+03:00."
    )
    ended_at: Timestamp | None = Field(
        default=None,
        description="Время прибытия/выселения с UTC-смещением. Необязательно; не раньше startedAt.",
    )

    @field_validator("started_at", "ended_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        try:
            return value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("timestamp is out of range in UTC") from error

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("endedAt must be on or after startedAt")
        return self


class TransferTicketRequest(TicketTimes):
    """Подтверждённый авиабилет, поезд или судно: две точки и время перемещения."""

    kind: Literal[TicketKind.PLANE, TicketKind.TRAIN, TicketKind.SHIP]
    departure: TicketPoint
    arrival: TicketPoint


class StayTicketRequest(TicketTimes):
    """Подтверждённая гостиница: точка stay, startedAt — заселение, endedAt — выселение."""

    kind: Literal[TicketKind.STAY]
    stay: TicketPoint


TicketApplyRequest = Annotated[
    TransferTicketRequest | StayTicketRequest, Field(discriminator="kind")
]


class TicketApplyResult(Base):
    points: list[Waypoint] = Field(
        description="Созданные или повторно использованные точки поездки."
    )
    transfer: Movement | None = Field(
        default=None,
        description="Сохранённое перемещение для plane/train/ship; null для гостиницы.",
    )
    event: Event | None = Field(
        default=None,
        description="Сохранённое событие stay для гостиницы; null для транспортного билета.",
    )
