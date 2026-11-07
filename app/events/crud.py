from pydantic import ValidationError as PydanticValidationError
from pydantic_core import MISSING
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


def get_trip_events(trip_key: int | None):
    return (
        select(EventTable)
        .where(EventTable.trip_id == trip_key)
        .execution_options(populate_existing=True)
    )


async def get_event_in_trip(
    session: Session,
    trip: TripTable,
    event_id: int,
) -> EventTable:
    event = await session.scalar(
        get_trip_events(trip.primary_key).where(EventTable.id == event_id)
    )
    if event is None:
        raise NotFoundError
    return event


async def get_waypoint_in_trip(
    session: Session,
    trip: TripTable,
    waypoint_id: int | None,
) -> WaypointTable | None:
    if waypoint_id is None:
        return None
    waypoint = await session.scalar(
        select(WaypointTable).where(
            WaypointTable.trip_id == trip.primary_key,
            WaypointTable.id == waypoint_id,
        )
    )
    if waypoint is None:
        raise NotFoundError
    return waypoint


def validate_trip_dates(trip: TripTable, event: EventBase) -> None:
    if event.started_at.date() < trip.started_on:
        raise ValueError("event starts before the trip")
    elif (
        trip.ended_on is not None
        and (event.ended_at or event.started_at).date() > trip.ended_on
    ):
        raise ValueError("event ends after the trip")


async def list_events(
    session: Session,
    owner_id: int,
    trip_id: int,
) -> list[Event]:
    trip = await get_owned_trip(session, owner_id, trip_id)
    rows = await session.scalars(
        get_trip_events(trip.primary_key).order_by(
            col(EventTable.started_at), col(EventTable.primary_key)
        )
    )
    return [Event.model_validate(event) for event in rows]


async def get_event(
    session: Session,
    owner_id: int,
    trip_id: int,
    event_id: int,
) -> Event:
    trip = await get_owned_trip(session, owner_id, trip_id)
    return Event.model_validate(await get_event_in_trip(session, trip, event_id))


async def create_event(
    session: Session,
    owner_id: int,
    trip_id: int,
    request: CreateEventRequest,
) -> Event:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    validate_trip_dates(trip, request)
    waypoint = await get_waypoint_in_trip(session, trip, request.waypoint_id)
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
    event = await get_event_in_trip(session, trip, event_id)
    changes = request.model_dump(exclude_unset=True, exclude={"waypoint_id"})

    try:
        validated = EventBase.model_validate(event, update=changes)
    except PydanticValidationError as error:
        raise ValidationError(errors=error.errors(include_input=False)) from error

    try:
        if {"started_at", "ended_at"} & changes.keys():
            validate_trip_dates(trip, validated)
    except ValueError as e:
        raise ValidationError(errors=[str(e)])

    if request.waypoint_id is not MISSING:
        event.waypoint = await get_waypoint_in_trip(session, trip, request.waypoint_id)

    session.add(changes)
    await session.commit()
    await session.refresh(event)
    return Event.model_validate(event)


async def delete_event(
    session: Session,
    owner_id: int,
    trip_id: int,
    event_id: int,
) -> None:
    trip = await get_owned_trip(session, owner_id, trip_id, lock=True)
    await session.delete(await get_event_in_trip(session, trip, event_id))
    await session.commit()
