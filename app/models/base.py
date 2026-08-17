from typing import Optional
from uuid import UUID, uuid4

from pydantic.alias_generators import to_camel
from sqlalchemy.orm import declared_attr
from sqlmodel import Field, SQLModel

from app.models.types.snowflake_id import DefaultSnowflakeId, SnowflakeId


class Base(SQLModel):
    class Config:
        alias_generator = to_camel
        populate_by_name = True
        from_attributes = True
        regex_engine = "python-re"

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
