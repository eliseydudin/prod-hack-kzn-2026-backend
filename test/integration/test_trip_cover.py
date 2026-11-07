from base64 import b64decode
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database.connection import engine
from app.main import app
from app.settings import settings

PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2cAAAAASUVORK5CYII="
)


def _login(client: TestClient) -> dict[str, str]:
    credentials = {"username": f"cover{uuid4().hex[:24]}", "password": "password"}
    response = client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/auth/login", data={"grant_type": "password", **credentials}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def storage_client():
    with TestClient(app) as client:
        try:
            yield client
        finally:
            assert client.portal is not None
            client.portal.call(engine.dispose)


@pytest.mark.skipif(settings.storage is None, reason="Requires test S3 storage")
def test_trip_cover_lifecycle(storage_client):
    client = storage_client
    owner = _login(client)
    other = _login(client)
    response = client.post(
        "/api/v1/trips",
        headers=owner,
        json={"name": "Казань", "startedOn": "2026-12-10"},
    )
    assert response.status_code == 201, response.text
    trip = f"/api/v1/trips/{response.json()['id']}"
    upload = f"{trip}/cover"
    image_headers = {**owner, "Content-Type": "image/png"}
    assert (
        client.put(upload, headers=image_headers, content=b"<svg/>").status_code == 415
    )
    assert (
        client.put(
            upload, headers=image_headers, content=PNG + bytes(5 * 1024 * 1024)
        ).status_code
        == 413
    )
    assert (
        client.put(upload, headers={**image_headers, **other}, content=PNG).status_code
        == 404
    )

    response = client.put(upload, headers=image_headers, content=PNG)
    assert response.status_code == 200, response.text
    first = response.json()["cover"]
    assert client.get(trip, headers=owner).json()["cover"] == first
    assert client.get(first).status_code == 401
    assert client.get(first, headers=other).status_code == 404
    response = client.get(first, headers=owner)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG

    response = client.put(upload, headers=image_headers, content=PNG)
    assert response.status_code == 200, response.text
    second = response.json()["cover"]
    assert second != first
    assert client.get(first, headers=owner).status_code == 404
    storage = app.state.storage

    def objects():
        return [
            obj.object_name
            for obj in storage.client.list_objects(
                storage.bucket, prefix=f"{upload.lstrip('/')}/", recursive=True
            )
        ]

    assert objects() == [second.lstrip("/")]
    response = client.patch(trip, headers=owner, json={"cover": None})
    assert response.status_code == 200, response.text
    assert response.json()["cover"] is None
    assert objects() == []

    assert client.put(upload, headers=image_headers, content=PNG).status_code == 200
    assert client.delete(trip, headers=owner).status_code == 204
    assert objects() == []
