from typing import Annotated, Any

from pydantic import PlainSerializer
from sqlmodel import BigInteger, Field

from app.utils.snowflake_id import generate_snowflake_id_in_base_epoch

SnowflakeId = Annotated[
    int,
    Field(
        ge=0,
        sa_type=BigInteger,
    ),
    PlainSerializer(str),
]


def DefaultSnowflakeId() -> Any:
    return Field(
        ge=0,
        default_factory=generate_snowflake_id_in_base_epoch,
        sa_type=BigInteger,
    )
