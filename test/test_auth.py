import pytest

from app.auth.tokens import encode_token, get_refresh_token
from app.main import app
from app.models.auth import AuthToken
from app.models.user import UserRole
from app.rate_limit import get_rate_limiter
from app.responses import AuthorizationError
from test.client import client


def test_refresh_rejects_missing_and_invalid_cookies(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_rate_limiter, lambda: None)
    assert client.post("/api/v1/auth/refresh").status_code == 401
    response = client.post(
        "/api/v1/auth/refresh", headers={"Cookie": "refresh_token=invalid"}
    )
    assert response.status_code == 401


def test_validation_response_does_not_echo_invalid_unicode(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_rate_limiter, lambda: None)
    response = client.post(
        "/api/v1/auth/register",
        content=rb'{"username":"\ud800","password":"test-password"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["errorCode"] == "ValidationFailed"
    assert "input" not in response.json()["errors"][0]
    assert "test-password" not in response.text


def test_refresh_rejects_access_token():
    token = encode_token(AuthToken(sub=1, role=UserRole.USER))
    with pytest.raises(AuthorizationError):
        get_refresh_token(token)
