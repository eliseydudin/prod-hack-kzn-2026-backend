import hashlib
import re
import unicodedata
import warnings
from dataclasses import dataclass
from io import BytesIO

import anyio
from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.integrations.google_drive.errors import (
    PhotoTooLargeError,
    UnsupportedPhotoError,
)
from app.responses import BadRequestError

_MAX_PIXELS = 25_000_000
_decoder_limit = anyio.CapacityLimiter(2)


@dataclass(frozen=True)
class Photo:
    data: bytes
    name: str
    mime_type: str
    sha256: str
    md5: str


def _inspect(data: bytes) -> tuple[str, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise UnsupportedPhotoError(
                        detail={"reason": "photo_format_unsupported"}
                    )
                if image.width * image.height > _MAX_PIXELS:
                    raise BadRequestError(
                        detail={"reason": "photo_dimensions_too_large"}
                    )
                image_format = image.format
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise BadRequestError(detail={"reason": "photo_invalid"}) from None
    return ("image/jpeg", ".jpg") if image_format == "JPEG" else ("image/png", ".png")


async def read_photo(upload: UploadFile, max_bytes: int) -> Photo:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PhotoTooLargeError(detail={"reason": "photo_too_large"})
    mime_type, extension = await anyio.to_thread.run_sync(
        _inspect, data, limiter=_decoder_limit
    )
    filename = (upload.filename or "photo").replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(
        char for char in filename if not unicodedata.category(char).startswith("C")
    )
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename).strip(" .")
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = (stem or "photo")[: 128 - len(extension)] + extension
    return Photo(
        data=data,
        name=name,
        mime_type=mime_type,
        sha256=hashlib.sha256(data).hexdigest(),
        md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
    )
