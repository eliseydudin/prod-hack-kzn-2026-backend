from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlmodel import col
from starlette.concurrency import run_in_threadpool

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
from app.storage import Storage


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


async def _remove_stored_cover(storage: Storage | None, cover: str | None) -> None:
    if storage is not None and cover and cover.startswith("/"):
        await run_in_threadpool(storage.remove, cover)


async def save_trip_cover(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    storage: Storage,
    cover: str,
    data: bytearray,
    content_type: str,
) -> Trip:
    trip = await get_owned_trip(session, owner, trip_id, lock=True)
    old_cover = trip.cover
    await run_in_threadpool(storage.put, cover, data, content_type)
    trip.cover = cover
    # shortcut: an uncertain commit can orphan the new object, reconcile if this recurs.
    await session.commit()
    await _remove_stored_cover(storage, old_cover)
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def update_trip(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    request: UpdateTripRequest,
    *,
    storage: Storage | None = None,
) -> Trip:
    trip = await get_owned_trip(session, owner, trip_id, lock=True)
    changes = request.model_dump(exclude_unset=True)
    try:
        TripBase.model_validate(trip, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error

    old_cover = trip.cover
    trip.sqlmodel_update(changes)
    await session.commit()
    if "cover" in changes:
        await _remove_stored_cover(storage, old_cover)
    await session.refresh(trip)
    return Trip.model_validate(trip)


async def delete_trip(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    *,
    storage: Storage | None = None,
) -> None:
    trip = await get_owned_trip(session, owner, trip_id, lock=True)
    old_cover = trip.cover
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
    await _remove_stored_cover(storage, old_cover)
