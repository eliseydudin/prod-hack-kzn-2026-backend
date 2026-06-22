from typing import Any

from app.responses.content import Content
from app.responses.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    GoneError,
    HTTPException,
    NotFoundError,
)


def responses(*args: Content | type[HTTPException]):
    """
    Construct responses for a FastAPI route.
    """

    result: dict[str | int, Any] = {}
    for arg in args:
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
]
