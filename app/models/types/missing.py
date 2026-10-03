from typing import Annotated, Union

from pydantic_core import MISSING
from sqlmodel import Field

type MaybeMissing[_T] = Annotated[Union[_T, MISSING], Field(default=MISSING)]
