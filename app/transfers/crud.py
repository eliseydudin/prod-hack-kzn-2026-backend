from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import aliased, raiseload
from sqlmodel import col, select

from app.database.session import Session
from app.models.movement import (
    CreateMovementRequest,
    Movement,
    MovementTable,
    UpdateMovementRequest,
)
from app.models.trip import TripTable
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import UserTable
from app.models.waypoint import WaypointTable
from app.responses import NotFoundError, ValidationError
from app.trips.crud import get_owned_trip

_start = aliased(WaypointTable)
_end = aliased(WaypointTable)
_WAYPOINT_IDS = {"starts_at_waypoint_id", "ends_at_waypoint_id"}


def _trip_transfers(trip: TripTable):
    return (
        select(MovementTable, _start, _end)
        .join(_start, _start.primary_key == MovementTable.starts_at_waypoint_id)  # type: ignore
        .join(_end, _end.primary_key == MovementTable.ends_at_waypoint_id)  # type: ignore
        .where(_start.trip == trip, _end.trip == trip)
        .options(raiseload("*"))
    )


async def _get_transfer(
    session: Session,
    trip: TripTable,
    transfer_id: SnowflakeId,
):
    result = await session.execute(
        _trip_transfers(trip).where(MovementTable.id == transfer_id)
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundError
    return row


def _as_movement(
    movement: MovementTable,
    start: WaypointTable,
    end: WaypointTable,
) -> Movement:
    return Movement.model_validate(
        movement,
        update={"starts_at_waypoint_id": start.id, "ends_at_waypoint_id": end.id},
    )


async def _waypoints(
    session: Session,
    trip: TripTable,
    request: CreateMovementRequest,
) -> tuple[WaypointTable, WaypointTable]:
    ids = {request.starts_at_waypoint_id, request.ends_at_waypoint_id}
    rows = await session.scalars(
        select(WaypointTable)
        .where(WaypointTable.trip == trip, col(WaypointTable.id).in_(ids))
        .options(raiseload("*"))
    )
    waypoints = {waypoint.id: waypoint for waypoint in rows}
    if waypoints.keys() != ids:
        raise NotFoundError
    return waypoints[request.starts_at_waypoint_id], waypoints[
        request.ends_at_waypoint_id
    ]


async def list_transfers(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
) -> list[Movement]:
    trip = await get_owned_trip(session, owner, trip_id)
    rows = await session.execute(
        _trip_transfers(trip).order_by(col(MovementTable.primary_key))
    )
    return [_as_movement(*row) for row in rows]


async def get_transfer(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    transfer_id: SnowflakeId,
) -> Movement:
    trip = await get_owned_trip(session, owner, trip_id)
    return _as_movement(*await _get_transfer(session, trip, transfer_id))


async def create_transfer(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    request: CreateMovementRequest,
) -> Movement:
    trip = await get_owned_trip(session, owner, trip_id)
    start, end = await _waypoints(session, trip, request)
    movement = MovementTable.model_validate(request.model_dump(exclude=_WAYPOINT_IDS))
    movement.starts_at_waypoint, movement.ends_at_waypoint = start, end
    session.add(movement)
    result = _as_movement(movement, start, end)
    await session.commit()
    return result


async def update_transfer(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    transfer_id: SnowflakeId,
    request: UpdateMovementRequest,
) -> Movement:
    trip = await get_owned_trip(session, owner, trip_id)
    movement, start, end = await _get_transfer(session, trip, transfer_id)
    changes = {"starts_at_waypoint_id": start.id, "ends_at_waypoint_id": end.id}
    changes.update(request.model_dump(exclude_unset=True))
    try:
        updated = CreateMovementRequest.model_validate(movement, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error

    start, end = await _waypoints(session, trip, updated)
    movement.sqlmodel_update(updated.model_dump(exclude=_WAYPOINT_IDS))
    movement.starts_at_waypoint, movement.ends_at_waypoint = start, end
    result = _as_movement(movement, start, end)
    await session.commit()
    return result


async def delete_transfer(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    transfer_id: SnowflakeId,
) -> None:
    trip = await get_owned_trip(session, owner, trip_id)
    movement, _, _ = await _get_transfer(session, trip, transfer_id)
    await session.delete(movement)
    await session.commit()
