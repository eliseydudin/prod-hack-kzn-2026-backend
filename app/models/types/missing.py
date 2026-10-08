from typing import Annotated

from pydantic_core import MISSING
from sqlmodel import Field

type MaybeMissing[T] = Annotated[T | MISSING, Field(default=MISSING)]
