from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import raiseload

from app.database.session import Session
from app.models.waypoint import (
    CreateWaypointRequest,
    UpdateWaypointRequest,
    Waypoint,
    WaypointTable,
)
from app.responses import ConflictError, NotFoundError
from app.trips.crud import get_owned_trip


def _trip_points(trip_key: int):
    return (
        select(WaypointTable)
        .where(WaypointTable.trip_id == trip_key)
        .options(raiseload("*"))
    )


async def _get_point(session: Session, trip_key: int, point_id: int) -> WaypointTable:
    result = await session.execute(
        _trip_points(trip_key).where(WaypointTable.id == point_id)
    )
    point = result.scalar_one_or_none()
    if point is None:
        raise NotFoundError
    return point


async def list_points(session: Session, owner_id: int, trip_id: int) -> list[Waypoint]:
    trip = await get_owned_trip(session, owner_id, trip_id)
    points = await session.scalars(
        _trip_points(trip.primary_key).order_by(WaypointTable.primary_key)
    )
    return [Waypoint.model_validate(point) for point in points]


async def get_point(
    session: Session, owner_id: int, trip_id: int, point_id: int
) -> Waypoint:
    trip = await get_owned_trip(session, owner_id, trip_id)
    return Waypoint.model_validate(
        await _get_point(session, trip.primary_key, point_id)
    )


async def create_point(
    session: Session, owner_id: int, trip_id: int, request: CreateWaypointRequest
) -> Waypoint:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    point = WaypointTable.model_validate(request, update={"trip_id": trip.primary_key})
    session.add(point)
    result = Waypoint.model_validate(point)
    await session.commit()
    return result


async def update_point(
    session: Session,
    owner_id: int,
    trip_id: int,
    point_id: int,
    request: UpdateWaypointRequest,
) -> Waypoint:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    point = await _get_point(session, trip.primary_key, point_id)
    point.sqlmodel_update(request.model_dump(exclude_unset=True))
    result = Waypoint.model_validate(point)
    await session.commit()
    return result


async def delete_point(
    session: Session, owner_id: int, trip_id: int, point_id: int
) -> None:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    point = await _get_point(session, trip.primary_key, point_id)
    try:
        await session.delete(point)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(detail={"reason": "point_has_transfers"}) from error
