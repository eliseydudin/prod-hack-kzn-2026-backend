from pydantic import ValidationError as PydanticValidationError
from sqlmodel import col, select

from app.database.session import Session
from app.events.crud import validate_trip_dates
from app.models.event import CreateEventRequest, Event, EventTable, EventType
from app.models.movement import (
    CreateMovementRequest,
    Movement,
    MovementBase,
    MovementTable,
)
from app.models.ticket import (
    StayTicketRequest,
    TicketApplyRequest,
    TicketApplyResult,
    TicketPoint,
    TransferTicketRequest,
)
from app.models.trip import TripTable
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import UserTable
from app.models.waypoint import Waypoint, WaypointTable
from app.responses import ValidationError
from app.trips.crud import get_owned_trip
from app.utils.snowflake_id import generate_snowflake_id_in_base_epoch


def _get_or_create_point(
    session: Session, trip: TripTable, point: TicketPoint
) -> WaypointTable:
    for waypoint in trip.waypoints:
        if (
            waypoint.name == point.name
            and waypoint.description == point.address
            and waypoint.latitude == point.latitude
            and waypoint.longitude == point.longitude
        ):
            return waypoint

    existing_ids = {waypoint.id for waypoint in trip.waypoints}
    waypoint = WaypointTable.model_validate(
        point, update={"trip": trip, "description": point.address}
    )
    while waypoint.id in existing_ids:
        waypoint.id = generate_snowflake_id_in_base_epoch()
    session.add(waypoint)
    return waypoint


async def _apply_transfer(
    session: Session, trip: TripTable, request: TransferTicketRequest
) -> TicketApplyResult:
    start = _get_or_create_point(session, trip, request.departure)
    end = _get_or_create_point(session, trip, request.arrival)
    transfer = CreateMovementRequest.model_validate(
        request,
        update={
            "transport_type": request.kind.value,
            "starts_at_waypoint_id": start.id,
            "ends_at_waypoint_id": end.id,
        },
    )

    row = None
    if start.primary_key is not None and end.primary_key is not None:
        row = await session.scalar(
            select(MovementTable)
            .where(
                col(MovementTable.starts_at_waypoint) == start,
                col(MovementTable.ends_at_waypoint) == end,
                col(MovementTable.transport_type) == transfer.transport_type,
                col(MovementTable.started_at) == transfer.started_at,
                col(MovementTable.ended_at) == transfer.ended_at,
            )
            .limit(1)
        )
    if row is None:
        row = MovementTable.model_validate(
            MovementBase.model_validate(transfer),
            update={"starts_at_waypoint": start, "ends_at_waypoint": end},
        )
        session.add(row)

    return TicketApplyResult(
        points=[Waypoint.model_validate(start), Waypoint.model_validate(end)],
        transfer=Movement.model_validate(
            row,
            update={"starts_at_waypoint_id": start.id, "ends_at_waypoint_id": end.id},
        ),
    )


async def _apply_stay(
    session: Session, trip: TripTable, request: StayTicketRequest
) -> TicketApplyResult:
    event = CreateEventRequest.model_validate(
        request, update={"title": request.stay.name, "type": EventType.STAY}
    )
    validate_trip_dates(trip, event)
    waypoint = _get_or_create_point(session, trip, request.stay)
    row = None
    if waypoint.primary_key is not None:
        row = await session.scalar(
            select(EventTable)
            .where(
                col(EventTable.trip) == trip,
                col(EventTable.waypoint) == waypoint,
                col(EventTable.type) == EventType.STAY,
                col(EventTable.title) == event.title,
                col(EventTable.started_at) == event.started_at,
                col(EventTable.ended_at) == event.ended_at,
            )
            .limit(1)
        )
    if row is None:
        row = EventTable.model_validate(
            event, update={"trip": trip, "waypoint": waypoint}
        )
        session.add(row)

    return TicketApplyResult(
        points=[Waypoint.model_validate(waypoint)], event=Event.model_validate(row)
    )


async def apply_ticket(
    session: Session,
    owner: UserTable,
    trip_id: SnowflakeId,
    request: TicketApplyRequest,
) -> TicketApplyResult:
    trip = await get_owned_trip(session, owner, trip_id, lock=True)
    try:
        if isinstance(request, TransferTicketRequest):
            result = await _apply_transfer(session, trip, request)
        else:
            result = await _apply_stay(session, trip, request)
        await session.commit()
    except PydanticValidationError as error:
        await session.rollback()
        raise ValidationError(errors=error.errors(include_input=False)) from error
    except Exception:
        await session.rollback()
        raise
    return result
