from sqlmodel import Field, Relationship

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
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
