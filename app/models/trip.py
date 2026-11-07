from datetime import date
from typing import TYPE_CHECKING, Annotated

from pydantic import (
    AfterValidator,
    HttpUrl,
    StringConstraints,
    TypeAdapter,
    field_serializer,
    model_validator,
)
from pydantic_core import MISSING
from sqlmodel import Field, Relationship, String, Text

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.types.missing import MaybeMissing
from app.models.types.time import CreatedAt, Timestamp, UpdatedAt
from app.models.user import UserTable

if TYPE_CHECKING:
    from app.models.waypoint import WaypointTable

_cover_url = TypeAdapter(HttpUrl)


def validate_cover_link(value: str) -> str:
    return str(_cover_url.validate_python(value))


CoverLink = Annotated[str, AfterValidator(validate_cover_link)]
TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]
DESCRIPTION_LIMIT = 512
TripDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_LIMIT)
]


class TripBase(Base):
    name: TrimmedText = Field(min_length=1, max_length=128)
    description: TripDescription | None = Field(
        default=None, sa_type=String(DESCRIPTION_LIMIT)
    )
    started_on: date
    ended_on: date | None = None
    cover: CoverLink | None = Field(default=None, sa_type=Text)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.ended_on is not None and self.ended_on < self.started_on:
            raise ValueError("endedOn must be on or after startedOn")
        return self


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


class Trip(TripExtraBase):
    @field_serializer("description")
    def serialize_description(self, value: str | None) -> str:
        return value or ""


class CreateTripRequest(TripBase):
    pass


class UpdateTripRequest(Base):
    name: MaybeMissing[TrimmedText] = Field(
        default=MISSING, min_length=1, max_length=128
    )
    description: MaybeMissing[TripDescription | None] = Field(default=MISSING)
    started_on: MaybeMissing[date] = Field(default=MISSING)
    ended_on: MaybeMissing[date | None] = Field(default=MISSING)
    cover: MaybeMissing[CoverLink | None] = Field(default=MISSING)
