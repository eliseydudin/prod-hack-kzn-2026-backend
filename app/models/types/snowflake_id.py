from typing import Annotated, Any

from pydantic import PlainSerializer
from sqlmodel import BigInteger, Field

from app.models.types.extra import Extra
from app.utils.snowflake_id import generate_snowflake_id_in_base_epoch

SnowflakeId = Annotated[
    int,
    Field(
        ge=0,
        le=2**64 - 1,
        sa_type=BigInteger,
        schema_extra=Extra(
            format="int64",
            example="1539303769798624214",
        ),
    ),
    PlainSerializer(str),
]


def DefaultSnowflakeId() -> Any:
    return Field(
        ge=0,
        le=2**64 - 1,
        default_factory=generate_snowflake_id_in_base_epoch,
        sa_type=BigInteger,
    )
