from typing import Annotated, TypeVar, Union

from pydantic_core import MISSING
from sqlmodel import Field

_T = TypeVar("_T")

MaybeMissing = Annotated[Union[_T, MISSING], Field(default=MISSING)]


def is_missing(value: MaybeMissing[_T]) -> bool:
    return value is MISSING
