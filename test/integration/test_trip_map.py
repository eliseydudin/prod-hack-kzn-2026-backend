from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.hasher import hash_password
from app.database import Session
from app.models.movement import CreateMovementRequest, UpdateMovementRequest
from app.models.trip import CreateTripRequest
from app.models.user import CreateUserRequest
from app.models.waypoint import CreateWaypointRequest, UpdateWaypointRequest, Waypoint
from app.points.crud import (
    create_point,
    delete_point,
    get_point,
    list_points,
    update_point,
)
from app.responses import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.settings import settings
from app.transfers.crud import (
    create_transfer,
    delete_transfer,
    get_transfer,
    list_transfers,
    update_transfer,
)
from app.trips.crud import create_trip, delete_trip
from app.users import create_user


@pytest.fixture
async def map_session():
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


async def _create_owner(session: Session) -> int:
    user = await create_user(
        session,
        CreateUserRequest(username=f"map{uuid4().hex[:24]}", password="password"),
        hash_password,
    )
    return user.primary_key


async def _create_trip(session: Session, owner_id: int) -> int:
    trip = await create_trip(
        session,
        owner_id,
        CreateTripRequest(
            name="Kazan", started_on=date(2026, 12, 10), ended_on=date(2026, 12, 13)
        ),
    )
    return trip.id


async def _create_point(
    session: Session,
    owner_id: int,
    trip_id: int,
    name: str,
    *,
    description: str | None = None,
) -> Waypoint:
    return await create_point(
        session,
        owner_id,
        trip_id,
        CreateWaypointRequest(
            name=name, latitude=55.8, longitude=49.1, description=description
        ),
    )


def _transfer_request(start_id: int, end_id: int) -> CreateMovementRequest:
    return CreateMovementRequest(
        transport_type="bus",
        started_at=datetime(2026, 12, 10, 8, tzinfo=UTC),
        ended_at=datetime(2026, 12, 10, 9, tzinfo=UTC),
        starts_at_waypoint_id=start_id,
        ends_at_waypoint_id=end_id,
    )


async def test_trip_points_and_transfers(map_session: Session):
    session = map_session
    owner_id = await _create_owner(session)
    outsider_id = await _create_owner(session)
    trip_id = await _create_trip(session, owner_id)
    other_trip_id = await _create_trip(session, owner_id)
    with pytest.raises(NotFoundError):
        await list_points(session, outsider_id, trip_id)

    start = await _create_point(session, owner_id, trip_id, "Station")
    end = await _create_point(session, owner_id, trip_id, "Hotel")
    foreign = await _create_point(session, owner_id, other_trip_id, "Airport")
    edited_point = await update_point(
        session, owner_id, trip_id, start.id, UpdateWaypointRequest(name="Main station")
    )
    assert edited_point == start.model_copy(update={"name": "Main station"})
    assert await get_point(session, owner_id, trip_id, start.id) == edited_point
    assert await list_points(session, owner_id, trip_id) == [edited_point, end]

    request = _transfer_request(start.id, end.id)
    with pytest.raises(NotFoundError):
        await create_transfer(
            session,
            owner_id,
            trip_id,
            request.model_copy(update={"ends_at_waypoint_id": foreign.id}),
        )
    created = await create_transfer(session, owner_id, trip_id, request)
    with pytest.raises(NotFoundError):
        await get_transfer(session, outsider_id, trip_id, created.id)
    with pytest.raises(NotFoundError):
        await get_transfer(session, owner_id, other_trip_id, created.id)
    assert await get_transfer(session, owner_id, trip_id, created.id) == created

    with pytest.raises(NotFoundError):
        await update_transfer(
            session,
            owner_id,
            trip_id,
            created.id,
            UpdateMovementRequest(starts_at_waypoint_id=foreign.id),
        )
    with pytest.raises(ValidationError):
        await update_transfer(
            session,
            owner_id,
            trip_id,
            created.id,
            UpdateMovementRequest(started_at=datetime(2026, 12, 10, 10, tzinfo=UTC)),
        )

    changes = {
        "transport_type": "train",
        "starts_at_waypoint_id": end.id,
        "ends_at_waypoint_id": start.id,
        "ended_at": None,
    }
    edited_transfer = await update_transfer(
        session, owner_id, trip_id, created.id, UpdateMovementRequest(**changes)
    )
    assert edited_transfer == created.model_copy(update=changes)
    assert await list_transfers(session, owner_id, trip_id) == [edited_transfer]

    with pytest.raises(ConflictError) as conflict:
        await delete_point(session, owner_id, trip_id, start.id)
    assert conflict.value.error_code == ErrorCode.POINT_HAS_TRANSFERS
    with pytest.raises(ConflictError):
        await delete_trip(session, owner_id, trip_id)
    await delete_transfer(session, owner_id, trip_id, created.id)
    assert await list_transfers(session, owner_id, trip_id) == []
    await delete_point(session, owner_id, trip_id, start.id)
    with pytest.raises(NotFoundError):
        await get_point(session, owner_id, trip_id, start.id)


async def test_patch_point_updates_coordinates_and_clears_description(
    map_session: Session,
):
    owner_id = await _create_owner(map_session)
    trip_id = await _create_trip(map_session, owner_id)
    original = await _create_point(
        map_session, owner_id, trip_id, "Station", description="Main entrance"
    )
    changes = {"latitude": 55.81, "longitude": 49.12, "description": None}

    updated = await update_point(
        map_session, owner_id, trip_id, original.id, UpdateWaypointRequest(**changes)
    )

    expected = original.model_copy(update=changes)
    assert updated == expected
    assert await get_point(map_session, owner_id, trip_id, original.id) == expected


async def test_patch_transfer_releases_replaced_point(map_session: Session):
    owner_id = await _create_owner(map_session)
    trip_id = await _create_trip(map_session, owner_id)
    start = await _create_point(map_session, owner_id, trip_id, "Station")
    end = await _create_point(map_session, owner_id, trip_id, "Hotel")
    replacement = await _create_point(map_session, owner_id, trip_id, "Airport")
    original = await create_transfer(
        map_session, owner_id, trip_id, _transfer_request(start.id, end.id)
    )
    changes = {"ends_at_waypoint_id": replacement.id}

    updated = await update_transfer(
        map_session, owner_id, trip_id, original.id, UpdateMovementRequest(**changes)
    )

    expected = original.model_copy(update=changes)
    assert updated == expected
    await delete_point(map_session, owner_id, trip_id, end.id)
    assert await get_transfer(map_session, owner_id, trip_id, original.id) == expected
