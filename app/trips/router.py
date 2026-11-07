from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Path, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.trip import (
    CreateTripRequest,
    Trip,
    TripCollaborators,
    TripInviteLink,
    UpdateTripRequest,
)
from app.models.types.snowflake_id import SnowflakeId
from app.responses import (
    AuthorizationError,
    ConflictError,
    Content,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
    responses,
)
from app.storage import StorageDep
from app.trips.crud import (
    create_trip,
    delete_trip,
    generate_trip_invite_link,
    get_collaborators_on_trip,
    get_owned_trip,
    get_trip,
    join_trip_by_invite_code,
    list_trips,
    save_trip_cover,
    update_trip,
)

router = APIRouter(prefix="/trips", tags=["trips"])

MAX_COVER_BYTES = 5 * 1024 * 1024
COVER_SIGNATURES = {
    "image/jpeg": ((0, b"\xff\xd8\xff"),),
    "image/png": ((0, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"),),
    "image/webp": ((0, b"RIFF"), (8, b"WEBPVP8")),
}


@router.get("/", responses=responses(AuthorizationError))
async def get_trips(session: SessionDep, user: CurrentUser) -> list[Trip]:
    return await list_trips(session, user)


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError),
)
async def post_trip(
    request: CreateTripRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await create_trip(session, user, request)


@router.get(
    "/{trip_id}",
    responses=responses(AuthorizationError, NotFoundError),
)
async def get_trip_by_id(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await get_trip(session, user, trip_id)


@router.get(
    "/{trip_id}/collaboratrs",
    responses=responses(AuthorizationError, NotFoundError),
)
async def get_trip_by_id_collaborators(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> TripCollaborators:
    return await get_collaborators_on_trip(session, user, trip_id)


@router.patch(
    "/{trip_id}",
    responses=responses(AuthorizationError, NotFoundError),
)
async def patch_trip(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    request: UpdateTripRequest,
    session: SessionDep,
    user: CurrentUser,
    storage: StorageDep,
) -> Trip:
    return await update_trip(session, user, trip_id, request, storage=storage)


@router.put(
    "/{trip_id}/cover",
    response_model=Trip,
    responses=responses(
        AuthorizationError,
        NotFoundError,
        PayloadTooLargeError,
        UnsupportedMediaTypeError,
        ServiceUnavailableError,
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in COVER_SIGNATURES
            },
        },
    },
)
async def put_trip_cover(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    storage: StorageDep,
) -> Trip:
    if storage is None:
        raise ServiceUnavailableError()

    content_type = (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
    )
    if content_type not in COVER_SIGNATURES:
        raise UnsupportedMediaTypeError()

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_COVER_BYTES:
            raise PayloadTooLargeError(detail={"reason": "cover_too_large"})
        data.extend(chunk)
    if not all(
        data.startswith(signature, offset)
        for offset, signature in COVER_SIGNATURES[content_type]
    ):
        raise UnsupportedMediaTypeError()

    cover = request.url_for("get_trip_cover", trip_id=trip_id, cover_id=uuid4()).path
    return await save_trip_cover(
        session, user, trip_id, storage, cover, data, content_type
    )


@router.get(
    "/{trip_id}/cover/{cover_id}",
    response_class=Response,
    responses=responses(
        AuthorizationError,
        NotFoundError,
        ServiceUnavailableError,
        *(Content(200, mime) for mime in COVER_SIGNATURES),
    ),
)
async def get_trip_cover(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    cover_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    storage: StorageDep,
) -> Response:
    trip = await get_owned_trip(session, user, trip_id)
    cover = request.url_for("get_trip_cover", trip_id=trip_id, cover_id=cover_id).path
    if trip.cover != cover:
        raise NotFoundError()
    if storage is None:
        raise ServiceUnavailableError()

    data, content_type = await run_in_threadpool(storage.get, cover)
    return Response(
        data,
        media_type=content_type
        if content_type in COVER_SIGNATURES
        else "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError, ConflictError),
)
async def remove_trip(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    session: SessionDep,
    user: CurrentUser,
    storage: StorageDep,
) -> None:
    await delete_trip(session, user, trip_id)


@router.post(
    "/{trip_id}/code/generate",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, NotFoundError),
)
async def generate_code(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    session: SessionDep,
    user: CurrentUser,
    max_uses: int = Query(default=10, ge=0, le=100),
) -> TripInviteLink:
    return await generate_trip_invite_link(session, user, trip_id, max_uses)


@router.post(
    "/code/redeem/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError, ConflictError),
)
async def join_trip(
    session: SessionDep,
    user: CurrentUser,
    code: str = Path(min_length=1, max_length=128),
):
    await join_trip_by_invite_code(session, user, code)
