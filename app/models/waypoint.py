from sqlmodel import Field, Relationship

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.journey import JourneyTable


class WaypointBase(Base):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )


class WaypointExtraBase(WaypointBase, SnowflakeIdBase):
    pass


class WaypointTable(WaypointExtraBase, PrimaryKeyBase, table=True):
    journey_id: int | None = Field(
        default=None,
        foreign_key="journey.primary_key",
    )
    journey: JourneyTable | None = Relationship(
        back_populates="waypoints",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class Waypoint(WaypointBase):
    pass
