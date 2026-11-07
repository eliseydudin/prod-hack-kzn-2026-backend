from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.database.session import Session
from app.models.trip import TripTable
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import UserTable
from app.models.waypoint import (
    CreateWaypointRequest,
    UpdateWaypointRequest,
    Waypoint,
    WaypointTable,
)
from app.responses import ConflictError, ErrorCode, NotFoundError
from app.trips.crud import get_owned_trip


def _trip_points(trip: TripTable):
    return select(WaypointTable).where(WaypointTable.trip == trip)


async def _get_point(
    session: Session,
    trip: TripTable,
    point_id: SnowflakeId,
) -> WaypointTable:
    result = await session.execute(
        _trip_points(trip).where(WaypointTable.id == point_id)
    )
    point = result.scalar_one_or_none()
    if point is None:
        raise NotFoundError
    return point


async def list_points(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
) -> list[Waypoint]:
    trip = await get_owned_trip(session, owner, trip_id)
    points = await session.scalars(
        _trip_points(trip).order_by(col(WaypointTable.primary_key))
    )
    return [Waypoint.model_validate(point) for point in points]


async def get_point(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    point_id: SnowflakeId,
) -> Waypoint:
    trip = await get_owned_trip(session, owner, trip_id)
    return Waypoint.model_validate(await _get_point(session, trip, point_id))


async def create_point(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    request: CreateWaypointRequest,
) -> Waypoint:
    trip = await get_owned_trip(session, owner, trip_id)
    point = WaypointTable.model_validate(request, update={"trip": trip})
    session.add(point)
    result = Waypoint.model_validate(point)
    await session.commit()
    return result


async def update_point(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    point_id: SnowflakeId,
    request: UpdateWaypointRequest,
) -> Waypoint:
    trip = await get_owned_trip(session, owner, trip_id)
    point = await _get_point(session, trip, point_id)
    point.sqlmodel_update(request.model_dump(exclude_unset=True))
    result = Waypoint.model_validate(point)
    await session.commit()
    return result


async def delete_point(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    point_id: SnowflakeId,
) -> None:
    trip = await get_owned_trip(session, owner, trip_id)
    point = await _get_point(session, trip, point_id)
    try:
        await session.delete(point)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(error_code=ErrorCode.POINT_HAS_TRANSFERS) from error
