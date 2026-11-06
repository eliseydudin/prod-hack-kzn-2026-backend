from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.waypoint import CreateWaypointRequest, UpdateWaypointRequest, Waypoint
from app.points.crud import (
    create_point,
    delete_point,
    get_point,
    list_points,
    update_point,
)
from app.responses import AuthorizationError, ConflictError, NotFoundError, responses

router = APIRouter(prefix="/trips/{tripId}/points", tags=["points"])
TripId = Annotated[int, Path(alias="tripId", gt=0, le=9223372036854775807)]
PointId = Annotated[int, Path(alias="pointId", gt=0, le=9223372036854775807)]


@router.get("", responses=responses(AuthorizationError, NotFoundError))
async def get_points(
    trip_id: TripId, session: SessionDep, user: CurrentUser
) -> list[Waypoint]:
    return await list_points(session, user.primary_key, trip_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, NotFoundError),
)
async def post_point(
    trip_id: TripId,
    request: CreateWaypointRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Waypoint:
    return await create_point(session, user.primary_key, trip_id, request)


@router.get("/{pointId}", responses=responses(AuthorizationError, NotFoundError))
async def get_point_by_id(
    trip_id: TripId, point_id: PointId, session: SessionDep, user: CurrentUser
) -> Waypoint:
    return await get_point(session, user.primary_key, trip_id, point_id)


@router.patch("/{pointId}", responses=responses(AuthorizationError, NotFoundError))
async def patch_point(
    trip_id: TripId,
    point_id: PointId,
    request: UpdateWaypointRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Waypoint:
    return await update_point(session, user.primary_key, trip_id, point_id, request)


@router.delete(
    "/{pointId}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError, ConflictError),
)
async def remove_point(
    trip_id: TripId, point_id: PointId, session: SessionDep, user: CurrentUser
) -> Response:
    await delete_point(session, user.primary_key, trip_id, point_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
