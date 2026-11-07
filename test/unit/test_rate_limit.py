from asyncio import Event, wait_for
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest import MonkeyPatch
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import OutOfMemoryError, ReadOnlyError

from app.auth.tokens import get_current_user
from app.database.connection import get_session
from app.main import app
from app.rate_limit import RateLimiter
from app.responses import RateLimitError, ServiceUnavailableError
from app.settings import settings
from test.client import client

LOGIN_FORM = {
    "grant_type": "password",
    "username": "traveler",
    "password": "wrong-password",
}


@pytest.fixture
def api_without_db(monkeypatch: MonkeyPatch):
    async def unused_session():
        return None

    monkeypatch.setitem(app.dependency_overrides, get_session, unused_session)
    return client


async def test_allows_request_at_quota_and_rejects_the_next_one():
    evaluate = AsyncMock(side_effect=[(3, 3000), (4, 2900)])
    limiter = RateLimiter(SimpleNamespace(eval=evaluate), window_seconds=60)

    await limiter.enforce("login", "192.0.2.1", 3)
    with pytest.raises(RateLimitError):
        await limiter.enforce("login", "192.0.2.1", 3)


async def test_counters_are_scoped_to_policy_and_subject():
    evaluate = AsyncMock(return_value=(1, 3000))
    limiter = RateLimiter(SimpleNamespace(eval=evaluate), window_seconds=60)

    await limiter.enforce("login", "192.0.2.1", 3)
    await limiter.enforce("login", "192.0.2.2", 3)
    await limiter.enforce("register", "192.0.2.1", 3)
    await limiter.enforce("login", "192.0.2.1", 3)

    first, other_ip, other_policy, repeated = (
        call.args[2] for call in evaluate.await_args_list
    )
    assert repeated == first
    assert len({first, other_ip, other_policy}) == 3


async def test_stalled_valkey_uses_failure_policy(monkeypatch: MonkeyPatch):
    async def stalled_eval(*args):
        await Event().wait()

    monkeypatch.setattr("app.rate_limit.VALKEY_TIMEOUT", 0.01)
    limiter = RateLimiter(SimpleNamespace(eval=stalled_eval), window_seconds=60)

    with pytest.raises(ServiceUnavailableError):
        await wait_for(
            limiter.enforce("login", "192.0.2.1", 3, fail_closed=True), timeout=1
        )
    await wait_for(limiter.enforce("user", "123", 3), timeout=1)


def test_limit_returns_shared_error_and_exposes_retry_after(
    monkeypatch: MonkeyPatch, api_without_db
):
    evaluate = AsyncMock(return_value=(2, 1501))
    limiter = RateLimiter(SimpleNamespace(eval=evaluate), window_seconds=60)
    monkeypatch.setattr(app.state, "rate_limiter", limiter, raising=False)
    monkeypatch.setattr(settings, "rate_limit_login", 1)

    origin = "http://localhost:3000"
    response = api_without_db.post(
        "/api/v1/auth/login", data=LOGIN_FORM, headers={"Origin": origin}
    )

    assert response.status_code == 429
    assert response.json()["errorCode"] == "RateLimited"
    assert response.headers["Retry-After"] == "2"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Expose-Headers"] == "Retry-After"
    assert api_without_db.get("/health").status_code == 200
    assert (
        api_without_db.options(
            "/api/v1/auth/login",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        ).status_code
        == 200
    )
    evaluate.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [ValkeyConnectionError, OutOfMemoryError, ReadOnlyError]
)
def test_valkey_outage_blocks_login_and_preserves_authenticated_routes(
    monkeypatch: MonkeyPatch, api_without_db, error
):
    evaluate = AsyncMock(side_effect=error)
    limiter = RateLimiter(SimpleNamespace(eval=evaluate), window_seconds=60)
    monkeypatch.setattr(app.state, "rate_limiter", limiter, raising=False)

    assert api_without_db.post("/api/v1/auth/login", data=LOGIN_FORM).status_code == 503

    resolve_user = AsyncMock(return_value=SimpleNamespace(id=123, primary_key=1))

    async def user_override():
        return await resolve_user()

    monkeypatch.setitem(app.dependency_overrides, get_current_user, user_override)
    monkeypatch.setattr("app.trips.router.list_trips", AsyncMock(return_value=[]))
    response = api_without_db.get("/api/v1/trips")

    assert response.status_code == 200
    assert response.json() == []
    resolve_user.assert_awaited_once()
