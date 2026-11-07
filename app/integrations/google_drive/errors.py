from pydantic.dataclasses import dataclass

from app.responses import HTTPException


@dataclass
class PhotoTooLargeError(HTTPException):
    status_code = 413


@dataclass
class UnsupportedPhotoError(HTTPException):
    status_code = 415
