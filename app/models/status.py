from sqlmodel import Field

from app.models.base import Base
from app.models.types.extra import Extra


class Status(Base):
    status: str = Field(
        schema_extra=Extra(
            example="ok",
        ),
    )
