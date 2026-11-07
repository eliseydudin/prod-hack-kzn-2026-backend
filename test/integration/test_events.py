from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Session
from app.events.crud import (
    create_event,
    delete_event,
    get_event,
    list_events,
    update_event,
)
from app.models.event import CreateEventRequest, EventType, UpdateEventRequest
from app.models.trip import CreateTripRequest
from app.models.user import UserTable
from app.models.waypoint import CreateWaypointRequest
from app.points.crud import create_point, delete_point
from app.responses import NotFoundError, ValidationError
from app.settings import settings
from app.trips.crud import create_trip


@pytest.fixture
async def event_session():
    engine = create_async_engine(settings.database_url.unicode_string())
    try:
        async with engine.connect() as connection:
            await connection.begin()
            async with Session(
                connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session:
                yield session
    finally:
        await engine.dispose()


async def test_event_lifecycle_and_trip_scope(event_session: Session):
    owner = UserTable(username=f"owner{uuid4().hex[:24]}", password=None)
    other = UserTable(username=f"other{uuid4().hex[:24]}", password=None)
    event_session.add_all([owner, other])
    await event_session.commit()
    trip_request = CreateTripRequest(
        name="Казань", started_on=date(2026, 12, 10), ended_on=date(2026, 12, 13)
    )
    trip = await create_trip(event_session, owner.primary_key, trip_request)
    other_trip = await create_trip(event_session, owner.primary_key, trip_request)
    point_request = CreateWaypointRequest(name="Место", latitude=55, longitude=49)
    waypoint = await create_point(
        event_session, owner.primary_key, trip.id, point_request
    )
    other_waypoint = await create_point(
        event_session, owner.primary_key, other_trip.id, point_request
    )

    event = await create_event(
        event_session,
        owner.primary_key,
        trip.id,
        CreateEventRequest(
            title="Ночная экскурсия",
            type=EventType.TOUR,
            started_at=datetime(2026, 12, 11, 23, 30),
            ended_at=datetime(2026, 12, 12, 1),
            waypoint_id=waypoint.id,
        ),
    )
    earlier = await create_event(
        event_session,
        owner.primary_key,
        trip.id,
        CreateEventRequest(
            title="Заселение",
            type=EventType.STAY,
            started_at=datetime(2026, 12, 10, 14),
        ),
    )
    assert event.waypoint_id == waypoint.id
    assert await list_events(event_session, owner.primary_key, trip.id) == [
        earlier,
        event,
    ]
    with pytest.raises(NotFoundError):
        await list_events(event_session, other.primary_key, trip.id)
    with pytest.raises(NotFoundError):
        await get_event(event_session, owner.primary_key, other_trip.id, event.id)
    with pytest.raises(NotFoundError):
        await update_event(
            event_session,
            owner.primary_key,
            trip.id,
            event.id,
            UpdateEventRequest(waypoint_id=other_waypoint.id),
        )
    with pytest.raises(ValidationError):
        await update_event(
            event_session,
            owner.primary_key,
            trip.id,
            event.id,
            UpdateEventRequest(started_at=datetime(2026, 12, 12, 2)),
        )

    updated = await update_event(
        event_session,
        owner.primary_key,
        trip.id,
        event.id,
        UpdateEventRequest(started_at=datetime(2026, 12, 11, 22), waypoint_id=None),
    )
    assert updated.waypoint_id is None
    assert updated.started_at == datetime(2026, 12, 11, 22)
    assert updated.ended_at == event.ended_at
    assert (
        await get_event(event_session, owner.primary_key, trip.id, event.id) == updated
    )

    await update_event(
        event_session,
        owner.primary_key,
        trip.id,
        event.id,
        UpdateEventRequest(waypoint_id=waypoint.id),
    )
    await delete_point(event_session, owner.primary_key, trip.id, waypoint.id)
    assert (
        await get_event(event_session, owner.primary_key, trip.id, event.id) == updated
    )

    await delete_event(event_session, owner.primary_key, trip.id, event.id)
    assert await list_events(event_session, owner.primary_key, trip.id) == [earlier]
