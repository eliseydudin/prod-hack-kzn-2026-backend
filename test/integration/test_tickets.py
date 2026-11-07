from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Session
from app.events.crud import list_events
from app.models.event import EventType
from app.models.ticket import (
    StayTicketRequest,
    TicketKind,
    TicketPoint,
    TransferTicketRequest,
)
from app.models.trip import CreateTripRequest
from app.models.user import UserTable
from app.points.crud import list_points
from app.responses import NotFoundError, ValidationError
from app.settings import settings
from app.tickets.crud import apply_ticket
from app.transfers.crud import list_transfers
from app.trips.crud import create_trip


@pytest.fixture
async def ticket_session():
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


async def test_ticket_apply_saves_owned_transfer_and_stay_once(ticket_session: Session):
    session = ticket_session
    owner = UserTable(username=f"owner{uuid4().hex[:24]}", password=None)
    other = UserTable(username=f"other{uuid4().hex[:24]}", password=None)
    session.add_all([owner, other])
    await session.commit()
    trip = await create_trip(
        session,
        owner,
        CreateTripRequest(
            name="Казань", started_on=date(2026, 12, 10), ended_on=date(2026, 12, 13)
        ),
    )
    flight = TransferTicketRequest(
        kind=TicketKind.PLANE,
        departure=TicketPoint(
            name="Шереметьево",
            address="Химки, Московская область",
            latitude=55.9726,
            longitude=37.4146,
        ),
        arrival=TicketPoint(
            name="Казань",
            address="Столбище, Татарстан",
            latitude=55.6062,
            longitude=49.2787,
        ),
        started_at=datetime(2026, 12, 10, 12, tzinfo=timezone(timedelta(hours=3))),
        ended_at=datetime(2026, 12, 10, 11, tzinfo=UTC),
    )
    with pytest.raises(NotFoundError):
        await apply_ticket(session, other, trip.id, flight)
    assert await list_points(session, owner, trip.id) == []
    for started_at in (
        datetime(2026, 12, 10),
        datetime.fromisoformat("0001-01-01T00:00:00+14:00"),
    ):
        with pytest.raises(ValidationError):
            await apply_ticket(
                session,
                owner,
                trip.id,
                flight.model_copy(update={"ended_at": None, "started_at": started_at}),
            )
    for change, field in (
        (
            {"started_at": datetime.fromisoformat("2026-12-10T01:00:00+03:00")},
            "startedAt",
        ),
        ({"ended_at": datetime(2026, 12, 14, tzinfo=UTC)}, "endedAt"),
    ):
        with pytest.raises(ValidationError) as rejected:
            await apply_ticket(
                session, owner, trip.id, flight.model_copy(update=change)
            )
        assert rejected.value.errors[0]["field"] == field
        assert rejected.value.errors[0]["code"] == "outside_trip"
    assert await list_points(session, owner, trip.id) == []

    saved = await apply_ticket(session, owner, trip.id, flight)
    assert await apply_ticket(session, owner, trip.id, flight) == saved
    assert saved.transfer is not None
    assert saved.transfer.transport_type == TicketKind.PLANE
    assert saved.transfer.starts_at_waypoint_id == saved.points[0].id
    assert saved.transfer.ends_at_waypoint_id == saved.points[1].id
    assert saved.transfer.started_at == datetime(2026, 12, 10, 9, tzinfo=UTC)
    assert saved.points[0].description == flight.departure.address
    assert await list_transfers(session, owner, trip.id) == [saved.transfer]

    hotel = StayTicketRequest(
        kind=TicketKind.STAY,
        stay=TicketPoint(
            name="Гостиница",
            address="Казань, улица Баумана, 1",
            latitude=55.79,
            longitude=49.12,
        ),
        started_at=datetime(2026, 12, 10, 14, tzinfo=UTC),
        ended_at=datetime(2026, 12, 12, 12, tzinfo=UTC),
    )
    stay = await apply_ticket(session, owner, trip.id, hotel)
    assert await apply_ticket(session, owner, trip.id, hotel) == stay
    assert stay.event is not None
    assert stay.event.type == EventType.STAY
    assert stay.event.waypoint_id == stay.points[0].id
    assert await list_events(session, owner, trip.id) == [stay.event]
    assert await list_points(session, owner, trip.id) == [*saved.points, *stay.points]
