from uuid import uuid4

from fastapi.testclient import TestClient

from app.database.connection import engine
from app.main import app


def test_change_password_and_delete_account():
    with TestClient(app, base_url="https://testserver") as client:
        try:
            _exercise_account_lifecycle(client)
        finally:
            assert client.portal is not None
            client.portal.call(engine.dispose)


def _exercise_account_lifecycle(client: TestClient):
    auth = "/api/v1/auth"
    credentials = {"username": f"account{uuid4().hex[:20]}", "password": "old-password"}
    assert client.post(f"{auth}/register", json=credentials).status_code == 201

    login = {"grant_type": "password", **credentials}
    response = client.post(f"{auth}/login", data=login)
    assert response.status_code == 200, response.text
    access = {"Authorization": f"Bearer {response.json()['access_token']}"}

    response = client.put(
        f"{auth}/me/password",
        headers=access,
        json={"currentPassword": "old-password"},
    )
    assert response.status_code == 422
    assert "old-password" not in response.text

    change = {"currentPassword": "wrong-password", "newPassword": "new-password"}
    response = client.put(f"{auth}/me/password", headers=access, json=change)
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "invalid_current_password"

    change["currentPassword"] = credentials["password"]
    response = client.put(f"{auth}/me/password", headers=access, json=change)
    assert response.status_code == 204, response.text
    assert client.post(f"{auth}/login", data=login).status_code == 401

    login["password"] = change["newPassword"]
    assert client.post(f"{auth}/login", data=login).status_code == 200
    current_refresh = {"Cookie": f"refresh_token={client.cookies['refresh_token']}"}
    response = client.delete(f"{auth}/me", headers=access)
    assert response.status_code == 204
    assert "refresh_token" not in client.cookies
    assert client.get(f"{auth}/me", headers=access).status_code == 401
    assert client.post(f"{auth}/refresh", headers=current_refresh).status_code == 401
    assert client.post(f"{auth}/login", data=login).status_code == 401
