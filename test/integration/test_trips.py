from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.hasher import hash_password
from app.database import Session
from app.models.trip import CreateTripRequest, UpdateTripRequest
from app.models.user import CreateUserRequest
from app.responses import NotFoundError, ValidationError
from app.settings import settings
from app.trips.crud import create_trip, delete_trip, get_trip, list_trips, update_trip
from app.users import create_user


@pytest.fixture
async def trip_session():
    engine = create_async_engine(settings.database_url.unicode_string())
    try:
        async with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def test_trip_lifecycle_and_access(trip_session: Session):
    owner = await create_user(
        trip_session,
        CreateUserRequest(username=f"owner{uuid4().hex[:24]}", password="password"),
        hash_password,
    )
    other = await create_user(
        trip_session,
        CreateUserRequest(username=f"other{uuid4().hex[:24]}", password="password"),
        hash_password,
    )
    trip = await create_trip(
        trip_session,
        owner,
        CreateTripRequest(
            name="Казань",
            started_on=date(2026, 12, 10),
            ended_on=date(2026, 12, 13),
            cover="https://example.com/kazan.jpg",
        ),
    )

    assert await list_trips(trip_session, other) == []
    with pytest.raises(NotFoundError):
        await get_trip(trip_session, other, trip.id)

    with pytest.raises(ValidationError):
        await update_trip(
            trip_session,
            owner,
            trip.id,
            UpdateTripRequest(started_on=date(2026, 12, 14)),
        )

    updated = await update_trip(
        trip_session,
        owner,
        trip.id,
        UpdateTripRequest(name="Казань и окрестности"),
    )
    assert updated.name == "Казань и окрестности"
    assert updated.started_on == trip.started_on
    assert updated.cover == "https://example.com/kazan.jpg"
    assert await get_trip(trip_session, owner, trip.id) == updated

    await delete_trip(trip_session, owner, trip.id)
    assert await list_trips(trip_session, owner) == []
