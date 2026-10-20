from enum import auto

from app.models.types.pascal_case_enum import PascalCaseEnum


class FailureCause(PascalCaseEnum):
    NO_ID_TOKEN = auto()
    """
    Raised when the returned access token doesn't contain an ID token.
    """
    NO_ACCESS_TOKEN = auto()
    """
    Raised when `httpx_auth` raises `GetAccessTokenError`
    """
    BAD_ID_TOKEN = auto()
    """
    The given ID token is not a valid JWT token.
    """
    VALIDATION_FAILED = auto()
    """
    Data extracted from the ID token is not a valid `GoogleProfile`
    """


class GoogleInteractionFailed(Exception):
    """
    Raised when social login via Google fails. Check `FailureCause` for a more detailed description.
    """

    def __init__(self, cause: FailureCause) -> None:
        super().__init__()
        self.cause = cause
