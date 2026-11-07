from typing import Optional
from uuid import UUID, uuid4

from pydantic import field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import declared_attr
from sqlmodel import Field, SQLModel

from app.models.types.snowflake_id import DefaultSnowflakeId, SnowflakeId


class Base(SQLModel):
    model_config = {
        "alias_generator": to_camel,
        "populate_by_name": True,
        "from_attributes": True,
        "regex_engine": "python-re",
        "hide_input_in_errors": True,
    }

    @field_validator("*", mode="after")
    @classmethod
    def validate_text(cls, value):
        if isinstance(value, str) and any(
            char == "\x00" or "\ud800" <= char <= "\udfff" for char in value
        ):
            raise ValueError("text contains unsupported Unicode characters")
        return value

    @declared_attr  # type: ignore
    def __tablename__(cls) -> str:  # type: ignore
        typename = cls.__name__.lower()
        if not typename.endswith("table"):
            raise ValueError("all table models should end with 'table'")

        return typename.removesuffix("table")


class PrimaryKeyBase(Base):
    primary_key: Optional[int] = Field(
        default=None,
        primary_key=True,
    )


class UUIDBase(Base):
    id: UUID = Field(default_factory=uuid4)


class SnowflakeIdBase(Base):
    id: SnowflakeId = DefaultSnowflakeId()
