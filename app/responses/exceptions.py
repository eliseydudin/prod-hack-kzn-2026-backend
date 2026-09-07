from typing import Any, ClassVar, Optional

from pydantic import TypeAdapter
from pydantic.alias_generators import to_camel
from pydantic.dataclasses import dataclass

from app.models.types.time import CreatedAt, Timestamp


@dataclass(kw_only=True, config={"alias_generator": to_camel})
class HTTPException(Exception):
    status_code: ClassVar[int] = 400

    detail: Optional[dict[str, Any]] = None
    message: str = "An error has occured"
    occured_at: Timestamp = CreatedAt()

    def dump_python(self) -> Any:
        return TypeAdapter(type(self)).dump_python(self, mode="json", by_alias=True)


@dataclass
class BadRequestError(HTTPException):
    message: str = "Bad request"


@dataclass
class AuthorizationError(HTTPException):
    status_code = 401
    message: str = "Unauthorized"


@dataclass
class ForbiddenError(HTTPException):
    status_code = 403
    message: str = "Forbidden"


@dataclass
class NotFoundError(HTTPException):
    status_code = 404
    message: str = "Not found"


@dataclass
class ConflictError(HTTPException):
    status_code = 409
    message: str = "Conflict"


@dataclass
class GoneError(HTTPException):
    status_code = 410
    message: str = "Gone"


@dataclass
class InternalServerError(HTTPException):
    status_code = 500
    message: str = "Internal server error"


@dataclass
class ServiceUnavailableError(HTTPException):
    status_code = 503
    message: str = "Service unavailable"
