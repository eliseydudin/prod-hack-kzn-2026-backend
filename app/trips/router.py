from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.trip import CreateTripRequest, Trip, UpdateTripRequest
from app.responses import AuthorizationError, ConflictError, NotFoundError, responses
from app.trips.crud import create_trip, delete_trip, get_trip, list_trips, update_trip

router = APIRouter(prefix="/trips", tags=["trips"])
TripId = Annotated[int, Path(gt=0, le=9223372036854775807)]


@router.get("", response_model=list[Trip], responses=responses(AuthorizationError))
async def get_trips(session: SessionDep, user: CurrentUser) -> list[Trip]:
    return await list_trips(session, user.primary_key)


@router.post(
    "",
    response_model=Trip,
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError),
)
async def post_trip(
    request: CreateTripRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await create_trip(session, user.primary_key, request)


@router.get(
    "/{trip_id}",
    response_model=Trip,
    responses=responses(AuthorizationError, NotFoundError),
)
async def get_trip_by_id(
    trip_id: TripId,
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await get_trip(session, user.primary_key, trip_id)


@router.patch(
    "/{trip_id}",
    response_model=Trip,
    responses=responses(AuthorizationError, NotFoundError),
)
async def patch_trip(
    trip_id: TripId,
    request: UpdateTripRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await update_trip(session, user.primary_key, trip_id, request)


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError, ConflictError),
)
async def remove_trip(
    trip_id: TripId,
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    await delete_trip(session, user.primary_key, trip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
