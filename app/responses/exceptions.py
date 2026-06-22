from functools import lru_cache
from typing import Any, ClassVar

from pydantic import Field, TypeAdapter
from pydantic.alias_generators import to_camel
from pydantic.dataclasses import dataclass

from app.models.types.time import Timestamp, utc_now


class TypeAdapterPropertyMeta(type):
    @property
    @lru_cache(1)
    def type_adapter(self):
        return TypeAdapter(self)


@dataclass(kw_only=True, config={"alias_generator": to_camel})
class HTTPException(Exception, metaclass=TypeAdapterPropertyMeta):
    status_code: ClassVar[int] = 400
    default_message: ClassVar[str] = "An error has occured"

    detail: dict[str, Any] = Field(default_factory=dict)
    message: str = default_message
    occured_at: Timestamp = Field(default_factory=utc_now)

    def __post_init__(self):
        if self.message == HTTPException.default_message:
            self.message = type(self).default_message

    def dump_json(self):
        return type(self).type_adapter.dump_python(self, mode="json")


@dataclass
class BadRequestError(HTTPException):
    default_message = "Bad request"


@dataclass
class AuthorizationError(HTTPException):
    status_code = 401
    default_message = "Unauthorized"


@dataclass
class ForbiddenError(HTTPException):
    status_code = 403
    default_message = "Forbidden"


@dataclass
class NotFoundError(HTTPException):
    status_code = 404
    default_message = "Not found"


@dataclass
class ConflictError(HTTPException):
    status_code = 409
    default_message = "Conflict"


@dataclass
class GoneError(HTTPException):
    status_code = 410
    default_message = "Gone"


@dataclass
class InternalServerError(HTTPException):
    status_code = 500
    default_message = "Internal server error"


@dataclass
class ServiceUnavailableError(HTTPException):
    status_code = 503
    default_message = "Service unavailable"
