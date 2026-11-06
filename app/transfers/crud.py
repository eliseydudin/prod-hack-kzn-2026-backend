from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import aliased, raiseload

from app.database.session import Session
from app.models.movement import (
    CreateMovementRequest,
    Movement,
    MovementTable,
    UpdateMovementRequest,
)
from app.models.waypoint import WaypointTable
from app.responses import NotFoundError, ValidationError
from app.trips.crud import get_owned_trip

_start = aliased(WaypointTable)
_end = aliased(WaypointTable)


def _trip_transfers(trip_key: int):
    return (
        select(MovementTable, _start.id, _end.id)
        .join(_start, _start.primary_key == MovementTable.starts_at_waypoint_id)
        .join(_end, _end.primary_key == MovementTable.ends_at_waypoint_id)
        .where(_start.trip_id == trip_key, _end.trip_id == trip_key)
        .options(raiseload("*"))
    )


async def _get_transfer(session: Session, trip_key: int, transfer_id: int):
    result = await session.execute(
        _trip_transfers(trip_key).where(MovementTable.id == transfer_id)
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundError
    return row


def _as_movement(movement: MovementTable, start_id: int, end_id: int) -> Movement:
    return Movement.model_validate(
        movement,
        update={"starts_at_waypoint_id": start_id, "ends_at_waypoint_id": end_id},
    )


async def _waypoint_keys(
    session: Session, trip_key: int, request: CreateMovementRequest
) -> dict[str, int]:
    ids = {request.starts_at_waypoint_id, request.ends_at_waypoint_id}
    rows = await session.execute(
        select(WaypointTable.id, WaypointTable.primary_key).where(
            WaypointTable.trip_id == trip_key, WaypointTable.id.in_(ids)
        )
    )
    keys = dict(rows.tuples().all())
    if keys.keys() != ids:
        raise NotFoundError
    return {
        "starts_at_waypoint_id": keys[request.starts_at_waypoint_id],
        "ends_at_waypoint_id": keys[request.ends_at_waypoint_id],
    }


async def list_transfers(
    session: Session, owner_id: int, trip_id: int
) -> list[Movement]:
    trip = await get_owned_trip(session, owner_id, trip_id)
    rows = await session.execute(
        _trip_transfers(trip.primary_key).order_by(MovementTable.primary_key)
    )
    return [_as_movement(*row) for row in rows]


async def get_transfer(
    session: Session, owner_id: int, trip_id: int, transfer_id: int
) -> Movement:
    trip = await get_owned_trip(session, owner_id, trip_id)
    return _as_movement(*await _get_transfer(session, trip.primary_key, transfer_id))


async def create_transfer(
    session: Session, owner_id: int, trip_id: int, request: CreateMovementRequest
) -> Movement:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    keys = await _waypoint_keys(session, trip.primary_key, request)
    movement = MovementTable.model_validate(request, update=keys)
    session.add(movement)
    result = _as_movement(
        movement, request.starts_at_waypoint_id, request.ends_at_waypoint_id
    )
    await session.commit()
    return result


async def update_transfer(
    session: Session,
    owner_id: int,
    trip_id: int,
    transfer_id: int,
    request: UpdateMovementRequest,
) -> Movement:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    movement, start_id, end_id = await _get_transfer(
        session, trip.primary_key, transfer_id
    )
    changes = {"starts_at_waypoint_id": start_id, "ends_at_waypoint_id": end_id}
    changes.update(request.model_dump(exclude_unset=True))
    try:
        updated = CreateMovementRequest.model_validate(movement, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error

    keys = await _waypoint_keys(session, trip.primary_key, updated)
    movement.sqlmodel_update(updated.model_dump() | keys)
    result = _as_movement(
        movement, updated.starts_at_waypoint_id, updated.ends_at_waypoint_id
    )
    await session.commit()
    return result


async def delete_transfer(
    session: Session, owner_id: int, trip_id: int, transfer_id: int
) -> None:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    movement, _, _ = await _get_transfer(session, trip.primary_key, transfer_id)
    await session.delete(movement)
    await session.commit()
