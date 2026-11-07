from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import raiseload
from sqlmodel import col, delete, desc, select
from starlette.concurrency import run_in_threadpool

from app.database.session import Session
from app.models.collaborator import CollaboratorTable
from app.models.event import EventTable
from app.models.trip import (
    CreateTripRequest,
    Trip,
    TripBase,
    TripCollaborators,
    TripInviteLink,
    TripInviteLinkTable,
    TripTable,
    UpdateTripRequest,
)
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import User, UserTable
from app.models.waypoint import WaypointTable
from app.responses import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.responses.exceptions.http import ErrorCode
from app.storage import Storage


def _user_key(user: UserTable) -> int | None:
    identity = inspect(user).identity
    return identity[0] if identity else user.primary_key


def _accessible_trip(user_id: int):
    return (col(TripTable.owner_id) == user_id) | col(TripTable.primary_key).in_(
        select(CollaboratorTable.trip_id).where(CollaboratorTable.user_id == user_id)
    )


async def get_owned_trip(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    *,
    lock: bool = False,
) -> TripTable:
    user_id = _user_key(owner)
    if user_id is None:
        raise NotFoundError
    query = (
        select(TripTable)
        .where(col(TripTable.id) == trip_id, _accessible_trip(user_id))
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(of=TripTable)
    trips = (await session.scalars(query.limit(2))).all()
    if len(trips) != 1:
        raise NotFoundError
    trip = trips[0]
    if trip.owner_id == user_id or any(
        user.primary_key == user_id for user in trip.collaborators
    ):
        return trip

    raise NotFoundError


async def list_trips(session: Session, owner: UserTable) -> list[Trip]:
    user_id = _user_key(owner)
    if user_id is None:
        raise NotFoundError
    result = await session.execute(
        select(TripTable)
        .where(_accessible_trip(user_id))
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
        updated = TripBase.model_validate(trip, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error

    if changes.keys() & {"started_on", "ended_on"}:
        events = await session.execute(
            select(EventTable.started_at, EventTable.ended_at).where(
                EventTable.trip_id == trip.primary_key
            )
        )
        if any(
            start.date() < updated.started_on
            or (
                updated.ended_on is not None
                and (end or start).date() > updated.ended_on
            )
            for start, end in events
        ):
            raise ValidationError(errors=["trip dates would exclude existing events"])

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
    try:
        trip = await get_owned_trip(session, owner, trip_id, lock=True)
        if trip.owner_id != _user_key(owner):
            raise ForbiddenError
        waypoint_key = await session.scalar(
            select(col(WaypointTable.primary_key))
            .where(col(WaypointTable.trip) == trip)
            .limit(1)
        )
        if waypoint_key is not None:
            raise ConflictError(detail={"reason": "trip_has_waypoints"})
        event_key = await session.scalar(
            select(col(EventTable.primary_key))
            .where(col(EventTable.trip_id) == trip.primary_key)
            .limit(1)
        )
        if event_key is not None:
            raise ConflictError(detail={"reason": "trip_has_events"})
        cover = trip.cover
        await session.execute(
            delete(TripInviteLinkTable).where(
                TripInviteLinkTable.trip_id == trip.primary_key
            )
        )
        await session.execute(
            delete(TripTable).where(
                col(TripTable.primary_key) == trip.primary_key,
                col(TripTable.owner_id) == trip.owner_id,
            )
        )
        await session.commit()

    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(detail={"reason": "trip_has_dependencies"}) from error
    except Exception:
        await session.rollback()
        raise
    await _remove_stored_cover(storage, cover)


async def generate_trip_invite_link(
    session: Session,
    user: UserTable,
    trip_id: SnowflakeId,
    max_uses: int,
):
    if not 1 <= max_uses <= 100:
        raise ValidationError(errors=["max_uses must be between 1 and 100"])
    trip = await get_owned_trip(session, user, trip_id, lock=True)
    invite_link = TripInviteLinkTable(max_uses=max_uses, uses=max_uses)
    trip.invite_links.append(invite_link)

    session.add_all((trip, invite_link))
    await session.commit()
    await session.refresh(invite_link)

    return TripInviteLink.model_validate(invite_link)


async def _get_locked_invite_link(session: Session, code: str, trip_key: int):
    query = (
        select(TripInviteLinkTable)
        .where(
            TripInviteLinkTable.code == code,
            TripInviteLinkTable.trip_id == trip_key,
        )
        .with_for_update()
        .options(raiseload("*"))
        .execution_options(populate_existing=True)
    )
    if res := await session.scalar(query):
        return res
    raise ConflictError(error_code=ErrorCode.EXPIRED_LINK)


async def join_trip_by_invite_code(session: Session, user: UserTable, code: str):
    try:
        trip_key = await session.scalar(
            select(TripInviteLinkTable.trip_id).where(TripInviteLinkTable.code == code)
        )
        if trip_key is None:
            raise ConflictError(error_code=ErrorCode.EXPIRED_LINK)
        trip = await session.scalar(
            select(TripTable)
            .where(TripTable.primary_key == trip_key)
            .with_for_update(of=TripTable)
            .execution_options(populate_existing=True)
        )
        if trip is None:
            raise ConflictError(error_code=ErrorCode.EXPIRED_LINK)
        link = await _get_locked_invite_link(session, code, trip_key)
        if link.uses <= 0:
            raise ConflictError(error_code=ErrorCode.EXPIRED_LINK)
        user_id = _user_key(user)
        if trip.owner_id == user_id or any(
            member.primary_key == user_id for member in trip.collaborators
        ):
            raise ConflictError(error_code=ErrorCode.USER_IS_ALREADY_A_COLLABORATOR)

        trip.collaborators.append(user)
        link.uses -= 1

        if link.uses == 0:
            await session.delete(link)
        await session.commit()
    except Exception:
        await session.rollback()
        raise


async def get_collaborators_on_trip(
    session: Session,
    user: UserTable,
    trip_id: SnowflakeId,
):
    trip = await get_owned_trip(session, user, trip_id)
    return TripCollaborators(
        owner=User.model_validate(trip.owner),
        collaborators=[User.model_validate(user) for user in trip.collaborators],
    )
