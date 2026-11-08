from dataclasses import dataclass
from io import BytesIO
from logging import getLogger
from os import environ
from typing import Annotated

import certifi
from fastapi import Depends, Request
from minio import Minio
from minio.error import MinioException, S3Error
from urllib3 import PoolManager, Timeout
from urllib3.exceptions import HTTPError

from app.responses import (
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
)
from app.settings import StorageSettings

logger = getLogger(__name__)

IMAGE_SIGNATURES = {
    "image/jpeg": ((0, b"\xff\xd8\xff"),),
    "image/png": ((0, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"),),
    "image/webp": ((0, b"RIFF"), (8, b"WEBPVP8")),
}


async def read_upload(
    request: Request,
    signatures: dict[str, tuple[tuple[int, bytes], ...]],
    max_bytes: int,
    *,
    too_large_reason: str,
) -> tuple[bytearray, str]:
    content_type = (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
    )
    if content_type not in signatures:
        raise UnsupportedMediaTypeError()

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > max_bytes:
            raise PayloadTooLargeError(detail={"reason": too_large_reason})
        data.extend(chunk)
    if not all(
        data.startswith(signature, offset)
        for offset, signature in signatures[content_type]
    ):
        raise UnsupportedMediaTypeError()
    return data, content_type


@dataclass
class Storage:
    client: Minio
    bucket: str

    def put(self, path: str, data: bytearray, content_type: str) -> None:
        try:
            self.client.put_object(
                self.bucket,
                path.lstrip("/"),
                BytesIO(data),
                len(data),
                content_type=content_type,
            )
        except (MinioException, HTTPError) as error:
            logger.warning("Storage upload failed for %s", path, exc_info=True)
            raise ServiceUnavailableError() from error

    def get(self, path: str) -> tuple[bytes, str]:
        try:
            obj = self.client.get_object(self.bucket, path.lstrip("/"))
            try:
                return obj.read(), obj.headers.get(
                    "Content-Type", "application/octet-stream"
                )
            finally:
                obj.close()
                obj.release_conn()
        except (MinioException, HTTPError) as error:
            if isinstance(error, S3Error) and error.code == "NoSuchKey":
                raise NotFoundError() from error
            logger.warning("Storage download failed for %s", path, exc_info=True)
            raise ServiceUnavailableError() from error

    def remove(self, path: str) -> None:
        try:
            self.client.remove_object(self.bucket, path.lstrip("/"))
        except MinioException, HTTPError:
            logger.warning(
                "Could not remove unused storage object %s", path, exc_info=True
            )


async def get_storage(request: Request) -> Storage | None:
    return getattr(request.app.state, "storage", None)


StorageDep = Annotated[Storage | None, Depends(get_storage)]


def setup_storage(config: StorageSettings) -> Storage:
    client = Minio(
        endpoint=config.endpoint,
        access_key=config.access_key.get_secret_value(),
        secret_key=config.secret_key.get_secret_value(),
        secure=config.secure,
        http_client=PoolManager(
            timeout=Timeout(connect=2, read=8),
            retries=1,
            maxsize=10,
            cert_reqs="CERT_REQUIRED",
            ca_certs=environ.get("SSL_CERT_FILE") or certifi.where(),
        ),
    )
    if not client.bucket_exists(config.bucket):
        try:
            client.make_bucket(config.bucket)
        except S3Error as error:
            if error.code != "BucketAlreadyOwnedByYou":
                raise
    return Storage(client, config.bucket)
