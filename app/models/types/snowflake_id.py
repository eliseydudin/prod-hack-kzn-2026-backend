from typing import Annotated

from pydantic import Field

from app.utils.snowflake_id import generate_snowflake_id_in_base_epoch

SnowflakeId = Annotated[int, Field(strict=True, ge=0, le=2**64 - 1)]
DefaultSnowflakeId = Annotated[
    int,
    Field(
        strict=True,
        ge=0,
        le=2**64 - 1,
        default_factory=generate_snowflake_id_in_base_epoch,
    ),
]
