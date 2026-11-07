from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.trip import CreateTripRequest, Trip, TripInviteLink, UpdateTripRequest
from app.models.types.snowflake_id import SnowflakeId
from app.responses import AuthorizationError, ConflictError, NotFoundError, responses
from app.trips.crud import (
    create_trip,
    delete_trip,
    generate_trip_invite_link,
    get_trip,
    join_trip_by_invite_code,
    list_trips,
    update_trip,
)

router = APIRouter(prefix="/trips", tags=["trips"])


@router.get("", responses=responses(AuthorizationError))
async def get_trips(session: SessionDep, user: CurrentUser) -> list[Trip]:
    return await list_trips(session, user)


@router.post(
    "",
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


@router.patch(
    "/{trip_id}",
    responses=responses(AuthorizationError, NotFoundError),
)
async def patch_trip(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    request: UpdateTripRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await update_trip(session, user, trip_id, request)


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError, ConflictError),
)
async def remove_trip(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    session: SessionDep,
    user: CurrentUser,
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
    code: str = Query(min_length=1, max_length=128),
):
    await join_trip_by_invite_code(session, user, code)
