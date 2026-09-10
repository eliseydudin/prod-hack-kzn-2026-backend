from typing import Annotated, Union

from pydantic_core import MISSING
from sqlmodel import Field

type MaybeMissing[_T] = Annotated[Union[_T, MISSING], Field(default=MISSING)]


def is_missing[_T](value: MaybeMissing[_T]) -> bool:
    return value is MISSING
