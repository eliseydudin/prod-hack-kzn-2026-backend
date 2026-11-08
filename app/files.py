from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.auth.tokens import CurrentUser
from app.rate_limit import limit_user_requests
from app.responses import (
    AuthorizationError,
    Content,
    NotFoundError,
    PayloadTooLargeError,
    RateLimitError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
    responses,
)
from app.storage import IMAGE_SIGNATURES, StorageDep, read_upload

router = APIRouter(
    prefix="/files",
    tags=["files"],
    dependencies=[Depends(limit_user_requests)],
    responses=responses(AuthorizationError, RateLimitError, ServiceUnavailableError),
)

MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_SIGNATURES = {**IMAGE_SIGNATURES, "application/pdf": ((0, b"%PDF-"),)}


@router.put(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(PayloadTooLargeError, UnsupportedMediaTypeError),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in FILE_SIGNATURES
            },
        },
    },
)
async def put_file(
    file_id: UUID,
    request: Request,
    user: CurrentUser,
    storage: StorageDep,
) -> None:
    if storage is None:
        raise ServiceUnavailableError()

    data, content_type = await read_upload(
        request, FILE_SIGNATURES, MAX_FILE_BYTES, too_large_reason="file_too_large"
    )
    await run_in_threadpool(
        storage.put, f"files/{user.id}/{file_id}", data, content_type
    )


@router.get(
    "/{file_id}",
    response_class=Response,
    responses=responses(
        NotFoundError, *(Content(200, mime) for mime in FILE_SIGNATURES)
    ),
)
async def get_file(file_id: UUID, user: CurrentUser, storage: StorageDep) -> Response:
    if storage is None:
        raise ServiceUnavailableError()

    data, content_type = await run_in_threadpool(
        storage.get, f"files/{user.id}/{file_id}"
    )
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if content_type not in IMAGE_SIGNATURES:
        headers["Content-Disposition"] = "attachment"
    return Response(
        data,
        media_type=content_type
        if content_type in FILE_SIGNATURES
        else "application/octet-stream",
        headers=headers,
    )
