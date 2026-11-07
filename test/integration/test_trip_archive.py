import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Session
from app.events.crud import create_event, list_events
from app.models.event import CreateEventRequest, EventType
from app.models.movement import CreateMovementRequest
from app.models.trip import CreateTripRequest
from app.models.trip_archive import TripArchive
from app.models.user import UserTable
from app.models.waypoint import CreateWaypointRequest
from app.points.crud import create_point, list_points
from app.responses import NotFoundError
from app.settings import settings
from app.transfers.crud import create_transfer, list_transfers
from app.trips.archive import export_trip, import_trip
from app.trips.crud import create_trip, get_owned_trip, get_trip


@pytest.fixture
async def archive_session():
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


async def test_import_creates_an_owned_copy_of_exported_trip(archive_session: Session):
    session = archive_session
    owner = UserTable(username=f"owner{uuid4().hex[:24]}", password=None)
    recipient = UserTable(username=f"other{uuid4().hex[:24]}", password=None)
    session.add_all([owner, recipient])
    await session.commit()
    request = CreateTripRequest(
        name="Казань",
        description="Маршрут на выходные",
        started_on=date(2026, 12, 10),
        ended_on=date(2026, 12, 13),
        cover="https://example.com/cover.jpg",
    )
    trip = await create_trip(session, owner, request)
    trip_row = await get_owned_trip(session, owner, trip.id)
    assert trip_row.waypoints == []
    start, end = [
        await create_point(
            session,
            owner,
            trip.id,
            CreateWaypointRequest(name=name, latitude=lat, longitude=lon),
        )
        for name, lat, lon in [("Вокзал", 55.8, 49.1), ("Гостиница", 55.7, 49.0)]
    ]
    event = await create_event(
        session,
        owner,
        trip.id,
        CreateEventRequest(
            title="Ночная экскурсия",
            type=EventType.TOUR,
            started_at=datetime(2026, 12, 11, 23, 30, tzinfo=UTC),
            ended_at=datetime(2026, 12, 12, 1, tzinfo=UTC),
            waypoint_id=end.id,
            booking_url="https://example.com/booking",
        ),
    )
    transfer = await create_transfer(
        session,
        owner,
        trip.id,
        CreateMovementRequest(
            transport_type="bus",
            started_at=datetime(2026, 12, 10, 8, tzinfo=UTC),
            ended_at=datetime(2026, 12, 10, 9, tzinfo=UTC),
            starts_at_waypoint_id=start.id,
            ends_at_waypoint_id=end.id,
        ),
    )

    with pytest.raises(NotFoundError):
        await export_trip(session, recipient, trip.id)
    archive = await export_trip(session, owner, trip.id)
    payload = archive.model_dump_json(by_alias=True)
    document = json.loads(payload)
    assert "cover" not in document["trip"]
    assert [point["id"] for point in document["points"]] == ["1", "2"]
    copied = await import_trip(
        session, recipient, TripArchive.model_validate_json(payload)
    )

    assert copied.id != trip.id
    assert CreateTripRequest.model_validate(copied) == request.model_copy(
        update={"cover": None}
    )
    assert await get_trip(session, recipient, copied.id) == copied
    with pytest.raises(NotFoundError):
        await get_trip(session, owner, copied.id)
    assert await get_trip(session, owner, trip.id) == trip

    new_start, new_end = await list_points(session, recipient, copied.id)
    assert {new_start.id, new_end.id}.isdisjoint({start.id, end.id})
    assert new_start == start.model_copy(update={"id": new_start.id})
    assert new_end == end.model_copy(update={"id": new_end.id})

    [new_event] = await list_events(session, recipient, copied.id)
    assert new_event.id != event.id
    assert new_event == event.model_copy(
        update={"id": new_event.id, "waypoint_id": new_end.id}
    )
    [new_transfer] = await list_transfers(session, recipient, copied.id)
    assert new_transfer.id != transfer.id
    assert new_transfer == transfer.model_copy(
        update={
            "id": new_transfer.id,
            "starts_at_waypoint_id": new_start.id,
            "ends_at_waypoint_id": new_end.id,
        }
    )
