from typing import Any

from app.responses.content import Content
from app.responses.exceptions.http import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    GoneError,
    GoogleInteractionFailed,
    HTTPException,
    InternalServerError,
    NotFoundError,
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
    return result


__all__ = [
    "responses",
    "HTTPException",
    "AuthorizationError",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "GoneError",
    "NotFoundError",
    "Content",
    "ServiceUnavailableError",
    "ValidationError",
    "GoogleInteractionFailed",
]
