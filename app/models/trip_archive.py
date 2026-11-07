from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, field_validator, model_validator
from sqlmodel import Field

from app.models.base import Base
from app.models.event import CreateEventRequest
from app.models.movement import CreateMovementRequest
from app.models.trip import TripDetails
from app.models.types.snowflake_id import SnowflakeId
from app.models.types.time import Timestamp
from app.models.waypoint import WaypointBase


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("timestamp is outside the supported UTC range") from error


ArchiveTimestamp = Annotated[Timestamp, AfterValidator(_normalize_utc)]
ArchiveEventTimestamp = Annotated[datetime, AfterValidator(_normalize_utc)]


class _ArchiveRecord(Base):
    @field_validator("*", mode="before")
    @classmethod
    def validate_archive_text(cls, value):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("text contains unsupported Unicode characters")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError(
                    "text contains unsupported Unicode characters"
                ) from error
        return value


class TripArchiveDetails(TripDetails, _ArchiveRecord):
    @field_validator("description", mode="before")
    @classmethod
    def normalize_legacy_description(cls, value):
        return None if value == "" else value


class TripArchivePoint(WaypointBase, _ArchiveRecord):
    id: SnowflakeId = Field(gt=0)
    visited: bool = False


class TripArchiveEvent(CreateEventRequest, _ArchiveRecord):
    # The original v1 archives used timezone-less event times.
    started_at: ArchiveEventTimestamp
    ended_at: ArchiveEventTimestamp | None = None
    waypoint_id: SnowflakeId | None = Field(default=None, gt=0)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_legacy_description(cls, value):
        return None if value == "" else value


class TripArchiveTransfer(CreateMovementRequest, _ArchiveRecord):
    started_at: ArchiveTimestamp
    ended_at: ArchiveTimestamp | None = None


class TripArchive(Base):
    version: Literal[1]
    trip: TripArchiveDetails
    points: list[TripArchivePoint]
    events: list[TripArchiveEvent]
    transfers: list[TripArchiveTransfer]

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value):
        if type(value) is not int:
            raise ValueError("archive version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_point_references(self):
        point_ids = {point.id for point in self.points}
        if len(point_ids) != len(self.points):
            raise ValueError("point IDs must be unique")
        if any(
            event.waypoint_id is not None and event.waypoint_id not in point_ids
            for event in self.events
        ):
            raise ValueError("event waypointId must refer to a point in the archive")
        if any(
            transfer.starts_at_waypoint_id not in point_ids
            or transfer.ends_at_waypoint_id not in point_ids
            for transfer in self.transfers
        ):
            raise ValueError("transfer endpoints must refer to points in the archive")
        return self
