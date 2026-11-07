from collections.abc import Iterator

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import raiseload
from sqlmodel import col, select

from app.database.session import Session
from app.events.crud import get_trip_events
from app.models.event import EventTable
from app.models.movement import MovementBase, MovementTable
from app.models.trip import Trip, TripTable
from app.models.trip_archive import (
    TripArchive,
    TripArchiveDetails,
    TripArchiveEvent,
    TripArchivePoint,
    TripArchiveTransfer,
)
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import UserTable
from app.models.waypoint import WaypointTable
from app.responses import ConflictError
from app.transfers.crud import get_trip_transfers
from app.trips.crud import get_owned_trip
from app.utils.snowflake_id import generate_snowflake_id_in_base_epoch


async def export_trip(
    session: Session, owner: UserTable, trip_id: SnowflakeId
) -> TripArchive:
    trip = await get_owned_trip(session, owner, trip_id)
    points = (
        await session.scalars(
            select(WaypointTable)
            .where(col(WaypointTable.trip) == trip)
            .order_by(col(WaypointTable.primary_key))
            .options(raiseload("*"))
            .execution_options(populate_existing=True)
        )
    ).all()
    point_ids = {
        point.primary_key: index for index, point in enumerate(points, start=1)
    }
    events = (
        await session.scalars(
            get_trip_events(trip).order_by(
                col(EventTable.started_at), col(EventTable.primary_key)
            )
        )
    ).all()
    transfers = (
        await session.execute(
            get_trip_transfers(trip).order_by(col(MovementTable.primary_key))
        )
    ).all()
    if any(
        start.primary_key not in point_ids or end.primary_key not in point_ids
        for _, start, end in transfers
    ) or any(
        event.waypoint_primary_key not in point_ids
        and event.waypoint is not None
        and event.waypoint.trip_id == trip.primary_key
        for event in events
    ):
        raise ConflictError(detail={"reason": "trip_changed_during_export"})
    try:
        return TripArchive(
            version=1,
            trip=TripArchiveDetails.model_validate(trip),
            points=[
                TripArchivePoint.model_validate(
                    point, update={"id": point_ids[point.primary_key]}
                )
                for point in points
            ],
            events=[
                TripArchiveEvent.model_validate(
                    event,
                    update={"waypoint_id": point_ids.get(event.waypoint_primary_key)},
                )
                for event in events
            ],
            transfers=[
                TripArchiveTransfer.model_validate(
                    movement,
                    update={
                        "starts_at_waypoint_id": point_ids[start.primary_key],
                        "ends_at_waypoint_id": point_ids[end.primary_key],
                    },
                )
                for movement, start, end in transfers
            ],
        )
    except PydanticValidationError as error:
        raise ConflictError(detail={"reason": "invalid_trip_data"}) from error


def _unique_child_ids(trip_id: SnowflakeId) -> Iterator[SnowflakeId]:
    issued = {trip_id}
    while True:
        candidate = generate_snowflake_id_in_base_epoch()
        if candidate not in issued:
            issued.add(candidate)
            yield candidate


async def import_trip(session: Session, owner: UserTable, archive: TripArchive) -> Trip:
    try:
        trip = TripTable.model_validate(archive.trip, update={"owner": owner})
        child_ids = _unique_child_ids(trip.id)
        points = {
            point.id: WaypointTable.model_validate(
                point, update={"id": next(child_ids), "trip": trip}
            )
            for point in archive.points
        }
        session.add_all([trip, *points.values()])
        session.add_all(
            EventTable.model_validate(
                event,
                update={
                    "id": next(child_ids),
                    "trip": trip,
                    "waypoint": points.get(event.waypoint_id),
                },
            )
            for event in archive.events
        )
        session.add_all(
            MovementTable.model_validate(
                MovementBase.model_validate(transfer),
                update={
                    "id": next(child_ids),
                    "starts_at_waypoint": points[transfer.starts_at_waypoint_id],
                    "ends_at_waypoint": points[transfer.ends_at_waypoint_id],
                },
            )
            for transfer in archive.transfers
        )
        await session.flush()
        result = Trip.model_validate(trip)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return result
