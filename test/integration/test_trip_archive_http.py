import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select

from app.auth.tokens import encode_token
from app.database import Session
from app.database.connection import engine as app_engine
from app.database.connection import get_session
from app.main import app
from app.models.auth import AuthToken
from app.models.event import EventTable
from app.models.movement import MovementTable
from app.models.trip import TripInviteLinkTable, TripTable
from app.models.trip_archive import TripArchive
from app.models.user import UserTable
from app.models.waypoint import WaypointTable
from app.settings import settings
from app.trips.archive import import_trip
from app.trips.archive_router import MAX_ARCHIVE_BYTES


def _document():
    return {
        "version": 1,
        "trip": {
            "name": "Казань 🧳",
            "description": "Два дня",
            "startedOn": "2026-12-10",
            "endedOn": "2026-12-13",
        },
        "points": [
            {"id": "7", "name": "Вокзал", "latitude": 55.8, "longitude": 49.1},
            {
                "id": "23",
                "name": "Гостиница",
                "latitude": 55.7,
                "longitude": 49.0,
                "visited": True,
            },
        ],
        "events": [
            {
                "title": "Экскурсия",
                "type": "tour",
                "startedAt": "2026-12-11T12:00:00+03:00",
                "endedAt": "2026-12-11T13:00:00+03:00",
                "waypointId": "23",
            },
            {"title": "Прогулка", "type": "walk", "startedAt": "2026-12-10T08:00:00Z"},
        ],
        "transfers": [
            {
                "transportType": "train",
                "startedAt": "2026-12-10T08:00:00+03:00",
                "endedAt": "2026-12-10T10:00:00+03:00",
                "startsAtWaypointId": "7",
                "endsAtWaypointId": "23",
            }
        ],
    }


@pytest.fixture
async def archive_http():
    engine = create_async_engine(settings.database_url.unicode_string())
    previous = app.dependency_overrides.get(get_session)
    try:
        async with engine.connect() as connection:
            await connection.begin()
            async with Session(
                connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session:
                users = [
                    UserTable(username=f"archive{uuid4().hex[:24]}", password=None)
                    for _ in range(3)
                ]
                session.add_all(users)
                await session.commit()
                headers = [
                    {
                        "Authorization": f"Bearer {encode_token(AuthToken(sub=user.id, role=user.role))}"
                    }
                    for user in users
                ]

                async def override_session():
                    yield session

                app.dependency_overrides[get_session] = override_session
                async with (
                    app.router.lifespan_context(app),
                    AsyncClient(
                        transport=ASGITransport(app=app, raise_app_exceptions=False),
                        base_url="http://test/api/v1/",
                    ) as client,
                ):
                    yield client, session, users, headers
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous
        await app_engine.dispose()
        await engine.dispose()


async def _counts(session):
    return [
        await session.scalar(select(func.count()).select_from(model))
        for model in (TripTable, WaypointTable, EventTable, MovementTable)
    ]


async def test_http_archive_preserves_graph_and_excludes_sharing(archive_http):
    client, session, users, (owner, collaborator, outsider) = archive_http
    response = await client.post("trips/import", json=_document(), headers=owner)
    assert response.status_code == 201, response.text
    trip_id = response.json()["id"]
    code = f"invite{uuid4().hex}"
    source = await session.scalar(select(TripTable).where(TripTable.id == int(trip_id)))
    source.collaborators.append(users[1])
    source.invite_links.append(TripInviteLinkTable(code=code, max_uses=2, uses=1))
    source.cover = f"/api/v1/trips/{trip_id}/cover/{uuid4()}"
    await session.commit()

    assert (await client.get(f"trips/{trip_id}/export")).status_code == 401
    assert (
        await client.get(f"trips/{trip_id}/export", headers=outsider)
    ).status_code == 404
    response = await client.get(f"trips/{trip_id}/export", headers=collaborator)
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["content-disposition"].endswith(f'{trip_id}.json"')
    document = response.json()
    assert set(document) == {"version", "trip", "points", "events", "transfers"}
    assert set(document["trip"]) == {"name", "description", "startedOn", "endedOn"}
    assert [point["id"] for point in document["points"]] == ["1", "2"]
    assert [point["visited"] for point in document["points"]] == [False, True]
    assert code not in response.text and source.cover not in response.text
    assert all("id" not in item for item in document["events"] + document["transfers"])

    document["trip"].update(
        ownerId=users[0].id,
        collaborators=[users[1].id],
        inviteLinks=[{"code": code}],
        cover=source.cover,
        id=trip_id,
    )
    response = await client.post("trips/import", json=document, headers=outsider)
    assert response.status_code == 201, response.text
    copied_id = response.json()["id"]
    assert copied_id != trip_id and response.json()["cover"] is None
    copied = await session.scalar(
        select(TripTable).where(TripTable.id == int(copied_id))
    )
    assert copied.owner == users[2]
    assert copied.collaborators == [] and copied.invite_links == []
    source_points = {point.id for point in source.waypoints}
    assert source_points.isdisjoint({point.id for point in copied.waypoints})
    assert (
        await client.get(f"trips/{copied_id}/export", headers=owner)
    ).status_code == 404
    document["trip"] = {
        key: value
        for key, value in document["trip"].items()
        if key in {"name", "description", "startedOn", "endedOn"}
    }
    response = await client.get(f"trips/{copied_id}/export", headers=outsider)
    assert response.status_code == 200, response.text
    assert response.json() == document


@pytest.mark.parametrize(
    "change",
    [
        ("points", 1, "id", 7),
        ("events", 0, "waypointId", "8"),
        ("transfers", 0, "endsAtWaypointId", "8"),
        ("points", 0, "id", "0"),
        ("points", 0, "id", str(2**63)),
        ("points", 0, "latitude", 90.1),
        ("points", 0, "latitude", float("nan")),
        ("points", 0, "longitude", float("inf")),
        ("transfers", 0, "startedAt", "2026-12-10T08:00:00"),
        ("events", 0, "endedAt", "2026-12-10T00:00:00Z"),
        ("events", 0, "type", "invalid"),
        ("transfers", 0, "startedAt", "0001-01-01T00:00:00+14:00"),
        ("transfers", 0, "startedAt", "9999-12-31T23:59:59-14:00"),
    ],
)
async def test_http_invalid_archive_never_inserts_rows(archive_http, change):
    client, session, _, (owner, *_) = archive_http
    document = _document()
    collection, index, field, value = change
    document[collection][index][field] = value
    document["privateSecret"] = "must-not-be-echoed"
    before = await _counts(session)
    response = await client.post(
        "trips/import",
        content=json.dumps(document).encode("ascii"),
        headers={**owner, "Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert "must-not-be-echoed" not in response.text
    assert await _counts(session) == before


@pytest.mark.parametrize("unsafe", ["\ud800", "\x00"])
async def test_http_archive_rejects_unsafe_unicode_without_echo(archive_http, unsafe):
    client, session, _, (owner, *_) = archive_http
    document = _document()
    document["trip"]["name"] = unsafe
    document["privateSecret"] = "must-not-be-echoed"
    before = await _counts(session)
    response = await client.post(
        "trips/import",
        content=json.dumps(document).encode("ascii"),
        headers={**owner, "Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert "must-not-be-echoed" not in response.text
    assert await _counts(session) == before


async def test_http_archive_limit_applies_to_stream_without_content_length(
    archive_http,
):
    client, session, _, (owner, *_) = archive_http
    before = await _counts(session)

    async def chunks():
        for _ in range(17):
            yield b" " * (MAX_ARCHIVE_BYTES // 16)

    response = await client.post(
        "trips/import",
        content=chunks(),
        headers={**owner, "Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert await _counts(session) == before


async def test_http_archive_accepts_exact_body_limit_and_legacy_empty_descriptions(
    archive_http,
):
    client, _, _, (owner, *_) = archive_http
    document = _document()
    document["trip"]["description"] = ""
    document["events"][0]["description"] = ""
    payload = json.dumps(document).encode("ascii")
    response = await client.post(
        "trips/import",
        content=payload + b" " * (MAX_ARCHIVE_BYTES - len(payload)),
        headers={**owner, "Content-Type": "application/json"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["description"] is None
    response = await client.get(f"trips/{response.json()['id']}/export", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["trip"]["description"] is None
    assert all(event["description"] is None for event in response.json()["events"])


async def test_http_archive_does_not_export_an_oversized_document(
    archive_http, monkeypatch
):
    client, _, _, (owner, *_) = archive_http
    response = await client.post("trips/import", json=_document(), headers=owner)
    assert response.status_code == 201, response.text
    trip_id = response.json()["id"]
    monkeypatch.setattr("app.trips.archive_router.MAX_ARCHIVE_BYTES", 256)
    response = await client.get(f"trips/{trip_id}/export", headers=owner)
    assert response.status_code == 413, response.text


async def test_http_archive_imports_one_thousand_points(archive_http):
    client, _, _, (owner, *_) = archive_http
    document = _document()
    document["points"] = [
        {"id": str(index), "name": f"Точка {index}", "latitude": 55, "longitude": 49}
        for index in range(1, 1001)
    ]
    response = await client.post("trips/import", json=document, headers=owner)
    assert response.status_code == 201, response.text
    trip_id = response.json()["id"]
    response = await client.get(f"trips/{trip_id}/points", headers=owner)
    assert response.status_code == 200, response.text
    assert (
        len(response.json()) == len({point["id"] for point in response.json()}) == 1000
    )
    response = await client.get(f"trips/{trip_id}/export", headers=owner)
    assert response.status_code == 200, response.text
    assert len(response.json()["points"]) == 1000


async def test_import_rolls_back_database_failure_after_graph_flush(
    archive_http, monkeypatch
):
    _, session, (owner, *_), _ = archive_http
    original_flush = session.flush
    before = await _counts(session)

    async def failed_flush(*args, **kwargs):
        await original_flush(*args, **kwargs)
        await session.execute(text("SELECT 1 / 0"))

    with monkeypatch.context() as patch:
        patch.setattr(session, "flush", failed_flush)
        with pytest.raises(Exception, match="division by zero"):
            await import_trip(session, owner, TripArchive.model_validate(_document()))
    assert await _counts(session) == before


async def test_import_rolls_back_cancellation_after_graph_flush(
    archive_http, monkeypatch
):
    _, session, (owner, *_), _ = archive_http
    original_flush = session.flush
    before = await _counts(session)

    async def cancelled_flush(*args, **kwargs):
        await original_flush(*args, **kwargs)
        asyncio.current_task().cancel()
        await asyncio.sleep(0)

    with monkeypatch.context() as patch:
        patch.setattr(session, "flush", cancelled_flush)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.create_task(
                import_trip(session, owner, TripArchive.model_validate(_document()))
            )
    assert await _counts(session) == before


async def test_http_archive_preserves_dates_and_normalizes_event_times(archive_http):
    client, _, _, (owner, *_) = archive_http
    document = _document()
    document["events"] = [
        {
            "title": "Граница дня",
            "type": "walk",
            "startedAt": "2026-12-10T00:30:00+14:00",
        },
        {
            "title": "Событие из старого архива",
            "type": "walk",
            "startedAt": "2026-12-10T13:15:00",
        },
    ]
    response = await client.post("trips/import", json=document, headers=owner)
    assert response.status_code == 201, response.text
    trip_id = response.json()["id"]
    response = await client.get(f"trips/{trip_id}/export", headers=owner)
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported["trip"]["startedOn"] == document["trip"]["startedOn"]
    assert [event["startedAt"] for event in exported["events"]] == [
        "2026-12-09T10:30:00Z",
        "2026-12-10T13:15:00Z",
    ]
    response = await client.post("trips/import", json=exported, headers=owner)
    assert response.status_code == 201, response.text
    assert response.json()["id"] != trip_id


async def test_archive_child_id_collisions_are_remapped(archive_http, monkeypatch):
    _, session, (owner, *_), _ = archive_http
    candidates = iter([71, 71, 72, 72, 73, 73, 74, 74, 75])
    monkeypatch.setattr(
        "app.trips.archive.generate_snowflake_id_in_base_epoch",
        lambda: next(candidates),
    )
    copied = await import_trip(
        session, owner, TripArchive.model_validate(deepcopy(_document()))
    )
    trip = await session.scalar(select(TripTable).where(TripTable.id == copied.id))
    assert {point.id for point in trip.waypoints} == {71, 72}
    events = (
        await session.scalars(select(EventTable).where(EventTable.trip == trip))
    ).all()
    assert {event.id for event in events} == {73, 74}
    assert {event.waypoint.id for event in events if event.waypoint is not None} == {72}
    movement = await session.scalar(select(MovementTable).where(MovementTable.id == 75))
    assert movement.starts_at_waypoint.id == 71 and movement.ends_at_waypoint.id == 72
