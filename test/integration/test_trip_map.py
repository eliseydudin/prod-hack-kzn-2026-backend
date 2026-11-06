from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Session, get_session
from app.main import app
from app.settings import settings


@pytest.fixture
async def map_client(monkeypatch):
    engine = create_async_engine(settings.database_url.unicode_string())
    try:
        async with engine.connect() as connection:
            await connection.begin()

            async def get_test_session():
                async with Session(
                    connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    yield session

            monkeypatch.setitem(app.dependency_overrides, get_session, get_test_session)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test/api/v1/"
            ) as client:
                yield client
    finally:
        await engine.dispose()


async def _register_and_login(client: AsyncClient) -> dict[str, str]:
    credentials = {"username": f"map{uuid4().hex[:24]}", "password": "password"}
    (await client.post("auth/register", json=credentials)).raise_for_status()
    login = await client.post(
        "auth/login", data={**credentials, "grant_type": "password"}
    )
    token = login.raise_for_status().json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_trip_points_and_transfers(map_client: AsyncClient):
    client = map_client
    outsider = await _register_and_login(client)
    client.headers.update(await _register_and_login(client))
    trip = {"name": "Kazan", "startedOn": "2026-12-10", "endedOn": "2026-12-13"}
    trip_id = (await client.post("trips", json=trip)).raise_for_status().json()["id"]
    other_id = (await client.post("trips", json=trip)).raise_for_status().json()["id"]
    points = f"trips/{trip_id}/points"
    transfers = f"trips/{trip_id}/transfers"
    assert (await client.get(points, headers=outsider)).status_code == 404

    coordinates = {"latitude": 55.8, "longitude": 49.1}
    start = await client.post(points, json={**coordinates, "name": "Station"})
    assert start.status_code == 201, start.text
    end = await client.post(points, json={**coordinates, "name": "Hotel"})
    start_id = start.json()["id"]
    end_id = end.raise_for_status().json()["id"]
    foreign = await client.post(
        f"trips/{other_id}/points", json={**coordinates, "name": "Airport"}
    )
    foreign_id = foreign.raise_for_status().json()["id"]
    point = f"{points}/{start_id}"
    edited_point = await client.patch(point, json={"name": "Main station"})
    assert edited_point.raise_for_status().json() == {
        **start.json(),
        "name": "Main station",
    }
    assert (await client.get(point)).json() == edited_point.json()
    assert (await client.get(points)).json() == [edited_point.json(), end.json()]

    transfer = {
        "transportType": "bus",
        "startedAt": "2026-12-10T08:00:00Z",
        "endedAt": "2026-12-10T09:00:00Z",
        "startsAtWaypointId": start_id,
        "endsAtWaypointId": end_id,
    }
    assert (
        await client.post(transfers, json={**transfer, "endsAtWaypointId": foreign_id})
    ).status_code == 404
    created = await client.post(transfers, json=transfer)
    assert created.status_code == 201, created.text
    transfer_id = created.json()["id"]
    item = f"{transfers}/{transfer_id}"
    assert (await client.get(item, headers=outsider)).status_code == 404
    assert (
        await client.get(f"trips/{other_id}/transfers/{transfer_id}")
    ).status_code == 404
    assert (await client.get(item)).json() == created.json()
    assert (
        await client.patch(item, json={"startsAtWaypointId": foreign_id})
    ).status_code == 404
    assert (
        await client.patch(item, json={"startedAt": "2026-12-10T10:00:00Z"})
    ).status_code == 422

    changes = {
        "transportType": "train",
        "startsAtWaypointId": end_id,
        "endsAtWaypointId": start_id,
        "endedAt": None,
    }
    edited_transfer = await client.patch(item, json=changes)
    assert edited_transfer.raise_for_status().json() == {**created.json(), **changes}
    assert (await client.get(transfers)).json() == [edited_transfer.json()]
    assert (await client.delete(point)).status_code == 409
    assert (await client.delete(f"trips/{trip_id}")).status_code == 409
    assert (await client.delete(item)).status_code == 204
    assert (await client.get(transfers)).json() == []
    assert (await client.delete(point)).status_code == 204
    assert (await client.get(point)).status_code == 404
