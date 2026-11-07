from collections.abc import Sequence
from enum import auto
from typing import Any, ClassVar

from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter
from pydantic.alias_generators import to_camel
from pydantic.dataclasses import dataclass
from sqlmodel import Field

from app.models.types.pascal_case_enum import PascalCaseEnum
from app.models.types.time import Timestamp, utc_now
from app.responses.exceptions.google import FailureCause


class ErrorCode(PascalCaseEnum):
    ERROR_OCCURED = auto()
    AUTHORIZATION_ERROR = auto()
    NOT_FOUND = auto()
    CONFLICT = auto()
    GOOGLE_INTERACTION_FAILED = auto()
    VALIDATION_FAILED = auto()
    USERNAME_IS_ALREADY_IN_USE = auto()
    POINT_HAS_TRANSFERS = auto()
    RATE_LIMITED = auto()


@dataclass(kw_only=True, config={"alias_generator": to_camel, "validate_by_name": True})
class HTTPException(Exception):
    status_code: ClassVar[int] = 400

    detail: dict[str, Any] = Field(default_factory=dict)
    occured_at: Timestamp = Field(default_factory=utc_now)
    error_code: ErrorCode = ErrorCode.ERROR_OCCURED

    def dump_python(self) -> Any:
        return TypeAdapter(type(self)).dump_python(
            self, mode="json", by_alias=True, fallback=str
        )


@dataclass
class BadRequestError(HTTPException):
    pass


@dataclass
class AuthorizationError(HTTPException):
    status_code = 401
    error_code: ErrorCode = ErrorCode.AUTHORIZATION_ERROR


@dataclass
class ForbiddenError(HTTPException):
    status_code = 403
    error_code: ErrorCode = ErrorCode.AUTHORIZATION_ERROR


@dataclass
class NotFoundError(HTTPException):
    status_code = 404
    error_code: ErrorCode = ErrorCode.NOT_FOUND


@dataclass
class ConflictError(HTTPException):
    status_code = 409
    error_code: ErrorCode = ErrorCode.CONFLICT


@dataclass
class GoneError(HTTPException):
    status_code = 410
    error_code: ErrorCode = ErrorCode.NOT_FOUND


@dataclass
class PayloadTooLargeError(HTTPException):
    status_code = 413


@dataclass
class UnsupportedMediaTypeError(HTTPException):
    status_code = 415


@dataclass(kw_only=True, config={"alias_generator": to_camel, "validate_by_name": True})
class RateLimitError(HTTPException):
    status_code = 429
    error_code: ErrorCode = ErrorCode.RATE_LIMITED
    retry_after: int = Field(exclude=True)


@dataclass
class InternalServerError(HTTPException):
    status_code = 500


@dataclass
class ServiceUnavailableError(HTTPException):
    status_code = 503


@dataclass(kw_only=True, config={"alias_generator": to_camel})
class ValidationError(HTTPException):
    errors: Sequence[Any]
    status_code = 422
    error_code: ErrorCode = ErrorCode.VALIDATION_FAILED

    @classmethod
    def from_fastapi(cls, validation_error: RequestValidationError):
        return ValidationError(errors=validation_error.errors())


@dataclass(kw_only=True, config={"alias_generator": to_camel})
class GoogleInteractionFailed(HTTPException):
    cause: FailureCause
    status_code = 400
    error_code: ErrorCode = ErrorCode.GOOGLE_INTERACTION_FAILED
