from typing import Any

from app.responses.content import Content
from app.responses.exceptions.http import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    ErrorCode,
    ForbiddenError,
    GoneError,
    GoogleInteractionFailed,
    HTTPException,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationError,
)
from app.utils.iter import extend_with


def responses(*args: Content | type[HTTPException]):
    """
    Construct responses for a FastAPI route.
    """

    result: dict[str | int, Any] = {}
    for arg in extend_with(args, InternalServerError, ValidationError):
        if isinstance(arg, Content):
            if result.get(arg.status_code) is None:
                result[arg.status_code] = {"content": {}}
            result[arg.status_code]["content"][arg.content_type] = {}
        else:
            result[arg.status_code] = {"model": arg}
            if arg is RateLimitError:
                result[arg.status_code]["headers"] = {
                    "Retry-After": {"schema": {"type": "integer", "minimum": 1}}
                }
    return result


__all__ = [
    "responses",
    "HTTPException",
    "AuthorizationError",
    "BadRequestError",
    "ConflictError",
    "ErrorCode",
    "ForbiddenError",
    "GoneError",
    "NotFoundError",
    "RateLimitError",
    "Content",
    "ServiceUnavailableError",
    "ValidationError",
    "GoogleInteractionFailed",
]
