from datetime import date
from typing import TYPE_CHECKING, Annotated

from pydantic import (
    AfterValidator,
    HttpUrl,
    TypeAdapter,
    model_validator,
)
from pydantic_core import MISSING
from sqlmodel import Field, Relationship, Text

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.collaborator import CollaboratorTable
from app.models.types.missing import MaybeMissing
from app.models.types.time import CreatedAt, Timestamp, UpdatedAt
from app.models.user import User, UserTable
from app.utils.unique_code import generate_unique_code

if TYPE_CHECKING:
    from app.models.waypoint import WaypointTable

_cover_url = TypeAdapter(HttpUrl)


def validate_cover_link(value: str) -> str:
    return str(_cover_url.validate_python(value))


CoverLink = Annotated[str, AfterValidator(validate_cover_link)]


class TripDetails(Base):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )
    started_on: date
    ended_on: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.ended_on is not None and self.ended_on < self.started_on:
            raise ValueError("endedOn must be on or after startedOn")
        return self


class TripBase(TripDetails):
    cover: str | None = Field(default=None, sa_type=Text)


class TripExtraBase(TripBase, SnowflakeIdBase):
    created_at: Timestamp | None = CreatedAt()
    updated_at: Timestamp = UpdatedAt()


class TripTable(TripExtraBase, PrimaryKeyBase, table=True):
    owner_id: int | None = Field(default=None, foreign_key="user.primary_key")
    owner: UserTable | None = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    waypoints: list[WaypointTable] = Relationship(
        back_populates="trip",
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    collaborators: list[UserTable] = Relationship(
        link_model=CollaboratorTable,
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    invite_links: list[TripInviteLinkTable] = Relationship(
        back_populates="trip",
        sa_relationship_kwargs={
            "lazy": "selectin",
        },
    )


class TripInviteLinkTable(PrimaryKeyBase, table=True):
    trip_id: int | None = Field(default=None, foreign_key="trip.primary_key")
    trip: TripTable | None = Relationship(
        back_populates="invite_links",
        sa_relationship_kwargs={
            "lazy": "selectin",
        },
    )
    code: str = Field(default_factory=generate_unique_code, unique=True)
    max_uses: int
    uses: int


class TripInviteLink(Base):
    code: str
    uses: int


class Trip(TripExtraBase):
    pass


class CreateTripRequest(TripBase):
    cover: CoverLink | None = None


class UpdateTripRequest(Base):
    name: MaybeMissing[str] = Field(default=MISSING, min_length=1, max_length=128)
    description: MaybeMissing[str | None] = Field(
        default=MISSING,
        min_length=1,
        max_length=512,
    )
    started_on: MaybeMissing[date] = Field(default=MISSING)
    ended_on: MaybeMissing[date | None] = Field(default=MISSING)
    cover: MaybeMissing[CoverLink | None] = Field(default=MISSING)


class TripCollaborators(Base):
    owner: User
    collaborators: list[User]
