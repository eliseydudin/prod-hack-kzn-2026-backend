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
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import UserTable
from app.models.waypoint import WaypointTable
from app.responses import ConflictError, NotFoundError, ValidationError


async def get_owned_trip(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    *,
    lock: bool = False,
) -> TripTable:
    query = select(TripTable).where(
        col(TripTable.id) == trip_id,
        col(TripTable.owner) == owner,
    )
    if lock:
        query = query.with_for_update(of=TripTable)
    trip = (await session.execute(query)).scalar_one_or_none()
    if trip is None:
        raise NotFoundError
    return trip


async def list_trips(session: Session, owner: UserTable) -> list[Trip]:
    result = await session.execute(
        select(TripTable)
        .where(col(TripTable.owner) == owner)
        .order_by(desc(col(TripTable.created_at)), desc(col(TripTable.primary_key)))
    )
    return [Trip.model_validate(trip) for trip in result.scalars().all()]


async def get_trip(session: Session, owner: UserTable, trip_id: SnowflakeId) -> Trip:
    return Trip.model_validate(await get_owned_trip(session, owner, trip_id))


async def create_trip(
    session: Session,
    owner: UserTable,
    request: CreateTripRequest,
) -> Trip:
    trip = TripTable.model_validate(request, update={"owner": owner})
    session.add(trip)
    await session.commit()
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def update_trip(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    request: UpdateTripRequest,
) -> Trip:
    trip = await get_owned_trip(session, owner, trip_id, lock=True)
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
    owner: UserTable,
    trip_id: SnowflakeId,
) -> None:
    trip = await get_owned_trip(session, owner, trip_id, lock=True)
    waypoint_key = await session.scalar(
        select(col(WaypointTable.primary_key))
        .where(col(WaypointTable.trip) == trip)
        .limit(1)
    )
    if waypoint_key is not None:
        raise ConflictError(detail={"reason": "trip_has_waypoints"})
    try:
        await session.execute(
            delete(TripTable).where(
                col(TripTable.primary_key) == trip.primary_key,
                col(TripTable.owner) == owner,
            )
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(detail={"reason": "trip_has_dependencies"}) from error
