from typing import Annotated

from pydantic import StringConstraints
from pydantic_core import MISSING
from sqlmodel import Field, Relationship

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.trip import TripTable
from app.models.types.missing import MaybeMissing


class WaypointLocation(Base):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    name: str = Field(min_length=1, max_length=128)


class WaypointBase(WaypointLocation):
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )


class WaypointExtraBase(WaypointBase, SnowflakeIdBase):
    visited: bool = False


class WaypointTable(WaypointExtraBase, PrimaryKeyBase, table=True):
    trip_id: int | None = Field(
        default=None,
        foreign_key="trip.primary_key",
    )
    trip: TripTable | None = Relationship(
        back_populates="waypoints",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class Waypoint(WaypointExtraBase):
    pass


class CreateWaypointRequest(WaypointBase):
    pass


class UpdateWaypointRequest(Base):
    latitude: MaybeMissing[float] = Field(default=MISSING, ge=-90.0, le=90.0)
    longitude: MaybeMissing[float] = Field(default=MISSING, ge=-180.0, le=180.0)
    name: MaybeMissing[str] = Field(default=MISSING, min_length=1, max_length=128)
    description: MaybeMissing[
        Annotated[str, StringConstraints(min_length=1, max_length=512)] | None
    ] = MISSING
    visited: MaybeMissing[bool] = MISSING
