from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.auth.tokens import get_current_user
from app.database.connection import get_session
from app.main import app
from app.models.user import UserRole
from app.product_metrics import ACTIVITY_KEY
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
