from sqlmodel import Field

from app.models.base import Base


class CollaboratorTable(Base, table=True):
    trip_id: int | None = Field(
        default=None,
        foreign_key="trip.primary_key",
        ondelete="CASCADE",
        primary_key=True,
    )
    user_id: int | None = Field(
        default=None,
        foreign_key="user.primary_key",
        ondelete="CASCADE",
        primary_key=True,
    )
