from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlmodel import col

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
    session: Session,
    owner_id: int | None,
    trip_id: int | None,
    *,
    lock: bool = False,
) -> TripTable:
    query = select(TripTable).where(
        TripTable.id == trip_id,  # type: ignore
        TripTable.owner_id == owner_id,  # type: ignore
    )
    if lock:
        query = query.with_for_update(of=TripTable)
    trip = (await session.execute(query)).scalar_one_or_none()
    if trip is None:
        raise NotFoundError
    return trip


async def list_trips(session: Session, owner_id: int | None) -> list[Trip]:
    result = await session.execute(
        select(TripTable)
        .where(TripTable.owner_id == owner_id)  # type: ignore
        .order_by(desc(col(TripTable.created_at)), desc(col(TripTable.primary_key)))
    )
    return [Trip.model_validate(trip) for trip in result.scalars().all()]


async def get_trip(session: Session, owner_id: int | None, trip_id: int | None) -> Trip:
    return Trip.model_validate(await get_owned_trip(session, owner_id, trip_id))


async def create_trip(
    session: Session,
    owner_id: int | None,
    request: CreateTripRequest,
) -> Trip:
    trip = TripTable.model_validate(request, update={"owner_id": owner_id})
    session.add(trip)
    await session.commit()
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def update_trip(
    session: Session,
    owner_id: int | None,
    trip_id: int | None,
    request: UpdateTripRequest,
) -> Trip:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    changes = request.model_dump(exclude_unset=True)
    try:
        TripBase.model_validate(trip, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error

    trip.sqlmodel_update(changes)
    await session.commit()
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def delete_trip(
    session: Session,
    owner_id: int | None,
    trip_id: int | None,
) -> None:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    waypoint_key = await session.scalar(
        select(col(WaypointTable.primary_key))
        .where(WaypointTable.trip_id == trip.primary_key)  # type: ignore
        .limit(1)
    )
    if waypoint_key is not None:
        raise ConflictError(detail={"reason": "trip_has_waypoints"})
    try:
        await session.execute(
            delete(TripTable).where(
                TripTable.primary_key == trip.primary_key,  # type: ignore
                TripTable.owner_id == owner_id,  # type: ignore
            )
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(detail={"reason": "trip_has_dependencies"}) from error
