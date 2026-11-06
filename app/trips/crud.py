from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import raiseload

from app.database.session import Session
from app.models.trip import (
    CreateTripRequest,
    Trip,
    TripBase,
    TripTable,
    UpdateTripRequest,
)
from app.models.waypoint import WaypointTable
from app.responses import ConflictError, NotFoundError, ValidationError


async def get_owned_trip(
    session: Session, owner_id: int, trip_id: int, *, lock: bool = False
) -> TripTable:
    query = (
        select(TripTable)
        .where(TripTable.id == trip_id, TripTable.owner_id == owner_id)
        .options(raiseload("*"))
    )
    if lock:
        query = query.with_for_update(of=TripTable)
    trip = (await session.execute(query)).scalar_one_or_none()
    if trip is None:
        raise NotFoundError
    return trip


async def list_trips(session: Session, owner_id: int) -> list[Trip]:
    result = await session.execute(
        select(TripTable)
        .where(TripTable.owner_id == owner_id)
        .order_by(desc(TripTable.created_at), desc(TripTable.primary_key))
    )
    return [Trip.model_validate(trip) for trip in result.scalars().all()]


async def get_trip(session: Session, owner_id: int, trip_id: int) -> Trip:
    return Trip.model_validate(await get_owned_trip(session, owner_id, trip_id))


async def create_trip(
    session: Session, owner_id: int, request: CreateTripRequest
) -> Trip:
    trip = TripTable.model_validate(request, update={"owner_id": owner_id})
    session.add(trip)
    await session.commit()
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def update_trip(
    session: Session, owner_id: int, trip_id: int, request: UpdateTripRequest
) -> Trip:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    changes = request.model_dump(exclude_unset=True)
    try:
        TripBase.model_validate(trip, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors()) from error

    trip.sqlmodel_update(changes)
    await session.commit()
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def delete_trip(session: Session, owner_id: int, trip_id: int) -> None:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    waypoint_key = await session.scalar(
        select(WaypointTable.primary_key)
        .where(WaypointTable.trip_id == trip.primary_key)
        .limit(1)
    )
    if waypoint_key is not None:
        raise ConflictError(detail={"reason": "trip_has_waypoints"})
    try:
        await session.execute(
            delete(TripTable).where(
                TripTable.primary_key == trip.primary_key,
                TripTable.owner_id == owner_id,
            )
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(detail={"reason": "trip_has_dependencies"}) from error
