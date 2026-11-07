from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import raiseload, selectinload
from sqlmodel import col, select

from app.database.session import Session
from app.models.event import (
    CreateEventRequest,
    Event,
    EventBase,
    EventTable,
    UpdateEventRequest,
)
from app.models.trip import TripTable
from app.models.waypoint import WaypointTable
from app.responses import NotFoundError, ValidationError
from app.trips.crud import get_owned_trip


def _trip_events(trip_key: int):
    return (
        select(EventTable)
        .where(EventTable.trip_id == trip_key)
        .options(selectinload(EventTable.waypoint).raiseload("*"))
        .execution_options(populate_existing=True)
    )


async def _get_event(session: Session, trip: TripTable, event_id: int) -> EventTable:
    event = await session.scalar(
        _trip_events(trip.primary_key).where(EventTable.id == event_id)
    )
    if event is None:
        raise NotFoundError
    return event


async def _get_waypoint(
    session: Session, trip: TripTable, waypoint_id: int | None
) -> WaypointTable | None:
    if waypoint_id is None:
        return None
    waypoint = await session.scalar(
        select(WaypointTable)
        .where(
            WaypointTable.trip_id == trip.primary_key, WaypointTable.id == waypoint_id
        )
        .options(raiseload("*"))
    )
    if waypoint is None:
        raise NotFoundError
    return waypoint


def _validate_trip_dates(trip: TripTable, event: EventBase) -> None:
    if event.started_at.date() < trip.started_on:
        field, message = "startedAt", "Event starts before the trip"
    elif (
        trip.ended_on is not None
        and (event.ended_at or event.started_at).date() > trip.ended_on
    ):
        field, message = (
            "endedAt" if event.ended_at is not None else "startedAt",
            "Event ends after the trip",
        )
    else:
        return
    raise ValidationError(
        errors=[
            {"type": "event_trip_date_range", "loc": ["body", field], "msg": message}
        ]
    )


async def list_events(session: Session, owner_id: int, trip_id: int) -> list[Event]:
    trip = await get_owned_trip(session, owner_id, trip_id)
    rows = await session.scalars(
        _trip_events(trip.primary_key).order_by(
            col(EventTable.started_at), col(EventTable.primary_key)
        )
    )
    return [Event.model_validate(event) for event in rows]


async def get_event(
    session: Session, owner_id: int, trip_id: int, event_id: int
) -> Event:
    trip = await get_owned_trip(session, owner_id, trip_id)
    return Event.model_validate(await _get_event(session, trip, event_id))


async def create_event(
    session: Session, owner_id: int, trip_id: int, request: CreateEventRequest
) -> Event:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    _validate_trip_dates(trip, request)
    waypoint = await _get_waypoint(session, trip, request.waypoint_id)
    event = EventTable.model_validate(
        request, update={"trip_id": trip.primary_key, "waypoint": waypoint}
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return Event.model_validate(event)


async def update_event(
    session: Session,
    owner_id: int,
    trip_id: int,
    event_id: int,
    request: UpdateEventRequest,
) -> Event:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    event = await _get_event(session, trip, event_id)
    changes = request.model_dump(exclude_unset=True, exclude={"waypoint_id"})
    try:
        validated = EventBase.model_validate(event, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error
    if {"started_at", "ended_at"} & changes.keys():
        _validate_trip_dates(trip, validated)
    if "waypoint_id" in request.model_fields_set:
        event.waypoint = await _get_waypoint(session, trip, request.waypoint_id)
    event.sqlmodel_update(changes)
    await session.commit()
    await session.refresh(event)
    return Event.model_validate(event)


async def delete_event(
    session: Session, owner_id: int, trip_id: int, event_id: int
) -> None:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    await session.delete(await _get_event(session, trip, event_id))
    await session.commit()
