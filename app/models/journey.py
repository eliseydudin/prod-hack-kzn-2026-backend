from datetime import date
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.types.time import CreatedAt, Timestamp
from app.models.user import User, UserTable

if TYPE_CHECKING:
    from app.models.waypoint import WaypointTable


class JourneyBase(Base):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )
    started_on: date
    ended_on: date | None = None


class JourneyExtraBase(JourneyBase, SnowflakeIdBase):
    created_at: Timestamp = CreatedAt()


class JourneyTable(JourneyExtraBase, PrimaryKeyBase, table=True):
    owner_id: int | None = Field(default=None, foreign_key="user.primary_key")
    owner: UserTable | None = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    waypoints: list[WaypointTable] = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"},
        back_populates="journey",
    )


class Journey(JourneyExtraBase):
    owner: User
