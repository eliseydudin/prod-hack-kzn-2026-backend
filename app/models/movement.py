from datetime import UTC, datetime
from typing import Annotated, ClassVar

from pydantic import AwareDatetime, ConfigDict, field_validator, model_validator
from pydantic_core import MISSING, PydanticCustomError
from sqlmodel import Field, Relationship

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.types.missing import MaybeMissing
from app.models.types.snowflake_id import SnowflakeId
from app.models.types.time import Timestamp
from app.models.waypoint import WaypointTable


class MovementBase(Base):
    transport_type: str = Field(min_length=1, max_length=128)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )
    started_at: Timestamp
    ended_at: Timestamp | None = None


class MovementExtraBase(MovementBase, SnowflakeIdBase):
    pass


class MovementTable(MovementExtraBase, PrimaryKeyBase, table=True):
    starts_at_waypoint_id: int | None = Field(
        default=None,
        foreign_key="waypoint.primary_key",
    )
    ends_at_waypoint_id: int | None = Field(
        default=None,
        foreign_key="waypoint.primary_key",
    )

    starts_at_waypoint: WaypointTable | None = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "MovementTable.starts_at_waypoint_id",
            "lazy": "selectin",
        }
    )
    ends_at_waypoint: WaypointTable | None = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "MovementTable.ends_at_waypoint_id",
            "lazy": "selectin",
        }
    )


class Movement(MovementExtraBase):
    starts_at_waypoint_id: SnowflakeId
    ends_at_waypoint_id: SnowflakeId


WaypointId = Annotated[SnowflakeId, Field(gt=0, le=9223372036854775807)]


class CreateMovementRequest(MovementBase):
    model_config: ClassVar[ConfigDict] = {"extra": "forbid"}

    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    starts_at_waypoint_id: WaypointId
    ends_at_waypoint_id: WaypointId

    @field_validator("started_at", "ended_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise PydanticCustomError(
                "movement_time_range", "endedAt must be on or after startedAt"
            )
        return self


class UpdateMovementRequest(Base):
    model_config: ClassVar[ConfigDict] = {"extra": "forbid"}

    transport_type: MaybeMissing[str] = Field(
        default=MISSING, min_length=1, max_length=128
    )
    started_at: MaybeMissing[AwareDatetime]
    ended_at: MaybeMissing[AwareDatetime | None]
    starts_at_waypoint_id: MaybeMissing[WaypointId]
    ends_at_waypoint_id: MaybeMissing[WaypointId]
