from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.auth.tokens import get_current_user
from app.database.connection import get_session
from app.main import app
from app.models.collaborator import CollaboratorTable
from app.models.event import EventTable, EventType
from app.models.trip import TripTable
from app.models.user import UserRole, UserTable
from app.models.waypoint import WaypointTable
from app.product_metrics import ACTIVITY_KEY, _account_metrics
from app.rate_limit import RateLimiter, get_rate_limiter
from app.responses import NotFoundError
from test.client import client


def test_only_successful_trip_requests_record_activity(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=(1, 1000)), zadd=AsyncMock())
    overrides = app.dependency_overrides
    monkeypatch.setitem(overrides, get_session, lambda: None)
    monkeypatch.setitem(overrides, get_rate_limiter, lambda: RateLimiter(redis, 60))
    user = SimpleNamespace(id=123, role=UserRole.USER)
    monkeypatch.setitem(overrides, get_current_user, lambda: user)
    monkeypatch.setattr(
        "app.trips.router.list_trips", AsyncMock(side_effect=[[], NotFoundError(), []])
    )

    assert client.get("/api/v1/trips").status_code == 200
    assert client.get("/api/v1/trips").status_code == 404
    user.role = UserRole.ADMIN
    assert client.get("/api/v1/trips").status_code == 200

    redis.zadd.assert_awaited_once()
    key, scores = redis.zadd.await_args.args
    assert key == ACTIVITY_KEY
    assert scores.keys() == {"123"}
    assert redis.zadd.await_args.kwargs == {"gt": True}


async def test_planning_metrics_count_accounts_and_trips_once(monkeypatch):
    now = datetime(2026, 11, 8, tzinfo=UTC)
    old = now - timedelta(days=8)
    recent = now - timedelta(hours=1)
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine)
    monkeypatch.setattr("app.product_metrics.get_session_ctx", sessions)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with sessions.begin() as session:
            session.add_all(
                [
                    UserTable(primary_key=1, username="new", created_at=recent),
                    UserTable(primary_key=2, username="old", created_at=old),
                    UserTable(primary_key=3, username="empty", created_at=recent),
                    UserTable(
                        primary_key=4,
                        username="disabled",
                        created_at=recent,
                        enabled=False,
                    ),
                    UserTable(
                        primary_key=5,
                        username="admin",
                        created_at=recent,
                        role=UserRole.ADMIN,
                    ),
                ]
            )
            session.add_all(
                TripTable(
                    primary_key=pk,
                    owner_id=owner,
                    name="Trip",
                    started_on=now.date(),
                    created_at=created,
                )
                for pk, owner, created in [
                    (1, 1, recent),
                    (2, 1, recent),
                    (3, 2, old),
                    (4, 4, recent),
                    (5, 5, recent),
                ]
            )
            session.add(
                WaypointTable(
                    primary_key=1, trip_id=1, name="Stop", latitude=0, longitude=0
                )
            )
            session.add_all(
                EventTable(
                    trip_id=1,
                    waypoint_primary_key=1,
                    type=EventType.WALK,
                    title=title,
                    started_at=now,
                )
                for title in ("Morning walk", "Evening walk")
            )
            session.add(
                EventTable(
                    trip_id=3, type=EventType.OTHER, title="Meeting", started_at=now
                )
            )
            session.add_all(
                CollaboratorTable(trip_id=1, user_id=user_id) for user_id in (2, 3)
            )

        assert await _account_metrics(now) == {
            "enabled_accounts": 3,
            "new_accounts_24h": 3,
            "new_enabled_accounts_7d": 2,
            "trip_owners": 2,
            "trip_owners_with_mapped_events": 1,
            "new_accounts_with_trip_7d": 1,
            "new_accounts_with_plan_7d": 1,
            "repeat_trip_owners": 1,
            "trips": 3,
            "trips_created_7d": 2,
            "trips_with_plan": 2,
            "shared_trips": 1,
        }
    finally:
        await engine.dispose()
