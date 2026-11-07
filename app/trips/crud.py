import warnings

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, desc, select
from starlette.concurrency import run_in_threadpool

from app.database.session import Session
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
from app.responses import ConflictError, NotFoundError, ValidationError
from app.responses.exceptions.http import ErrorCode, InternalServerError
from app.storage import Storage


async def get_owned_trip(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    *,
    lock: bool | None = None,
) -> TripTable:
    if lock is not None:
        warnings.warn("`lock` on `get_owned_trip` is deprecated")

    query = select(TripTable).where(col(TripTable.id) == trip_id)
    trip = (await session.execute(query)).scalar()

    if trip is not None and (owner == trip.owner or owner in trip.collaborators):
        return trip

    raise NotFoundError


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
    trip = await get_owned_trip(session, owner, trip_id)
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
    trip = await get_owned_trip(session, owner, trip_id)
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


async def generate_trip_invite_link(
    session: Session,
    user: UserTable,
    trip_id: SnowflakeId,
    max_uses: int,
):
    trip = await get_owned_trip(session, user, trip_id)
    invite_link = TripInviteLinkTable(max_uses=max_uses, uses=max_uses)
    trip.invite_links.append(invite_link)

    session.add_all((trip, invite_link))
    await session.commit()
    await session.refresh(invite_link)

    return TripInviteLink.model_validate(invite_link)


async def get_link_by_code(session: Session, code: str):
    query = select(TripInviteLinkTable).where(TripInviteLinkTable.code == code)
    if res := await session.scalar(query):
        return res
    raise ConflictError(error_code=ErrorCode.EXPIRED_LINK)


async def join_trip_by_invite_code(session: Session, user: UserTable, code: str):
    link = await get_link_by_code(session, code)
    if link.uses <= 0:
        raise ConflictError(error_code=ErrorCode.EXPIRED_LINK)

    if trip := link.trip:
        if user in trip.collaborators or user == trip.owner:
            raise ConflictError(error_code=ErrorCode.USER_IS_ALREADY_A_COLLABORATOR)

        link.trip.collaborators.append(user)
        link.uses -= 1

        if link.uses == 0:
            await session.delete(link)
        else:
            session.add(link)

        session.add(user)
        await session.commit()
    else:
        raise InternalServerError


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
