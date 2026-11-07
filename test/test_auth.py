import pytest

from app.auth.tokens import encode_token, get_refresh_token
from app.models.auth import AuthToken
from app.models.user import UserRole
from app.responses import AuthorizationError
from test.client import client


def test_refresh_rejects_missing_and_invalid_cookies():
    assert client.post("/api/v1/auth/refresh").status_code == 401
    response = client.post(
        "/api/v1/auth/refresh", headers={"Cookie": "refresh_token=invalid"}
    )
    assert response.status_code == 401


def test_refresh_rejects_access_token():
    token = encode_token(AuthToken(sub=1, role=UserRole.USER))
    with pytest.raises(AuthorizationError):
        get_refresh_token(token)
