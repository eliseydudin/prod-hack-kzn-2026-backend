from datetime import UTC

from pydantic import field_validator, model_validator
from pydantic_core import MISSING
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


class CreateMovementRequest(MovementBase):
    starts_at_waypoint_id: SnowflakeId
    ends_at_waypoint_id: SnowflakeId

    @field_validator("started_at", "ended_at")
    @classmethod
    def normalize_time(cls, value: Timestamp | None) -> Timestamp | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("endedAt must be on or after startedAt")
        return self


class UpdateMovementRequest(Base):
    transport_type: MaybeMissing[str] = Field(
        default=MISSING, min_length=1, max_length=128
    )
    started_at: MaybeMissing[Timestamp]
    ended_at: MaybeMissing[Timestamp | None]
    starts_at_waypoint_id: MaybeMissing[SnowflakeId]
    ends_at_waypoint_id: MaybeMissing[SnowflakeId]
