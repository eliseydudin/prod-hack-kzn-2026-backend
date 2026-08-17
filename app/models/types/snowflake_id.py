from typing import Annotated, Any

from sqlmodel import BigInteger, Field

from app.utils.snowflake_id import generate_snowflake_id_in_base_epoch

SnowflakeId = Annotated[
    int,
    Field(
        ge=0,
        le=2**64 - 1,
        sa_type=BigInteger,
    ),
]


def DefaultSnowflakeId() -> Any:
    return Field(
        ge=0,
        le=2**64 - 1,
        default_factory=generate_snowflake_id_in_base_epoch,
        sa_type=BigInteger,
    )
