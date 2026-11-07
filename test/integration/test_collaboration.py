import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import col, delete, select

from app.auth.tokens import encode_token
from app.database.connection import get_session
from app.database.session import Session
from app.main import app
from app.models.auth import AuthToken
from app.models.collaborator import CollaboratorTable
from app.models.event import EventTable, EventType
from app.models.trip import TripInviteLinkTable, TripTable
from app.models.user import UserTable
from app.models.waypoint import WaypointTable
from app.rate_limit import rate_limit_lifespan
from app.settings import settings
from app.storage import get_storage


@pytest.fixture
async def collab_api():
    engine = create_async_engine(settings.database_url.unicode_string())
    maker = async_sessionmaker(engine, class_=Session, expire_on_commit=False)
    async with maker() as session:
        people = [
            UserTable(username=f"collab{uuid4().hex[:24]}", password=None)
            for _ in range(3)
        ]
        session.add_all(people)
        await session.flush()
        trip = TripTable(
            name="Shared audit",
            started_on=date(2026, 12, 10),
            ended_on=date(2026, 12, 13),
            owner=people[0],
        )
        session.add(trip)
        await session.commit()
        trip_id, trip_key = trip.id, trip.primary_key
        user_keys = [person.primary_key for person in people]
        headers = [
            {
                "Authorization": f"Bearer {encode_token(AuthToken(sub=p.id, role=p.role))}"
            }
            for p in people
        ]

    async def sessions():
        async with maker() as session:
            yield session

    storage = Mock()
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        async with (
            rate_limit_lifespan(),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client,
        ):
            yield SimpleNamespace(
                client=client,
                maker=maker,
                trip_id=trip_id,
                trip_key=trip_key,
                users=user_keys,
                headers=headers,
                base=f"/api/v1/trips/{trip_id}",
                storage=storage,
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        async with maker() as session:
            await session.execute(
                delete(EventTable).where(EventTable.trip_id == trip_key)
            )
            await session.execute(
                delete(WaypointTable).where(WaypointTable.trip_id == trip_key)
            )
            await session.execute(
                delete(CollaboratorTable).where(CollaboratorTable.trip_id == trip_key)
            )
            await session.execute(
                delete(TripInviteLinkTable).where(
                    TripInviteLinkTable.trip_id == trip_key
                )
            )
            await session.execute(
                delete(TripTable).where(TripTable.primary_key == trip_key)
            )
            await session.execute(
                delete(UserTable).where(col(UserTable.primary_key).in_(user_keys))
            )
            await session.commit()
        await engine.dispose()


async def _link(api, uses=2):
    code = uuid4().hex
    async with api.maker() as session:
        session.add(
            TripInviteLinkTable(
                trip_id=api.trip_key, code=code, max_uses=uses, uses=uses
            )
        )
        await session.commit()
    return code


def _redeem(api, code, actor):
    return api.client.post(
        f"/api/v1/trips/code/redeem/{code}", headers=api.headers[actor]
    )


async def _state(api):
    async with api.maker() as session:
        trip = await session.scalar(
            select(TripTable).where(TripTable.primary_key == api.trip_key)
        )
        members = list(
            (
                await session.scalars(
                    select(CollaboratorTable.user_id).where(
                        CollaboratorTable.trip_id == api.trip_key
                    )
                )
            ).all()
        )
        links = dict(
            (
                await session.execute(
                    select(TripInviteLinkTable.code, TripInviteLinkTable.uses).where(
                        TripInviteLinkTable.trip_id == api.trip_key
                    )
                )
            ).all()
        )
        return trip, members, links


async def _wait_for_locks(session, count=1):
    async with asyncio.timeout(5):
        while True:
            await session.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                )
            )
            if waiting >= count:
                return
            await asyncio.sleep(0.01)


async def test_collaboration_permissions_and_lists_use_membership(collab_api):
    api = collab_api
    for actor in (1, 2):
        assert (
            await api.client.get("/api/v1/trips/", headers=api.headers[actor])
        ).json() == []
        assert (
            await api.client.get(api.base, headers=api.headers[actor])
        ).status_code == 404
        assert (
            await api.client.patch(
                api.base, headers=api.headers[actor], json={"name": "Not allowed"}
            )
        ).status_code == 404
        assert (
            await api.client.delete(api.base, headers=api.headers[actor])
        ).status_code == 404
        assert (
            await api.client.post(
                f"{api.base}/code/generate", headers=api.headers[actor]
            )
        ).status_code == 404
    code = await _link(api)
    assert (await _redeem(api, code, 1)).status_code == 204
    shared = await api.client.get("/api/v1/trips/", headers=api.headers[1])
    assert [trip["id"] for trip in shared.json()] == [str(api.trip_id)]
    assert (await api.client.get(api.base, headers=api.headers[1])).status_code == 200
    edited = await api.client.patch(
        api.base, headers=api.headers[1], json={"name": "Member edit"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Member edit"
    invited = await api.client.post(
        f"{api.base}/code/generate", headers=api.headers[1], params={"max_uses": 3}
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["uses"] == 3
    denied = await api.client.delete(api.base, headers=api.headers[1])
    assert denied.status_code == 403, denied.text
    assert (await api.client.get(api.base, headers=api.headers[0])).status_code == 200
    assert (await api.client.get(api.base, headers=api.headers[2])).status_code == 404
    collaborators = await api.client.get(
        f"{api.base}/collaboratrs", headers=api.headers[1]
    )
    assert collaborators.status_code == 200, collaborators.text
    assert len(collaborators.json()["collaborators"]) == 1
    assert (
        collaborators.json()["owner"]["id"]
        != collaborators.json()["collaborators"][0]["id"]
    )


@pytest.mark.parametrize("uses", [1, 2])
async def test_concurrent_redeemers_cannot_exceed_max_uses(collab_api, uses):
    api = collab_api
    code = await _link(api, uses)
    replies = await asyncio.gather(_redeem(api, code, 1), _redeem(api, code, 2))
    expected = [204, 409] if uses == 1 else [204, 204]
    assert sorted(response.status_code for response in replies) == expected, [
        r.text for r in replies
    ]
    _, members, links = await _state(api)
    assert len(members) == uses
    assert len(set(members)) == uses
    assert links == {}


@pytest.mark.parametrize("same_code", [False, True])
async def test_member_races_do_not_consume_two_invitation_uses(collab_api, same_code):
    api = collab_api
    first = await _link(api, 2)
    second = first if same_code else await _link(api, 2)
    replies = await asyncio.gather(_redeem(api, first, 1), _redeem(api, second, 1))
    assert sorted(r.status_code for r in replies) == [204, 409], [
        r.text for r in replies
    ]
    _, members, links = await _state(api)
    assert members == [api.users[1]]
    assert sorted(links.values()) == ([1] if same_code else [1, 2])
    assert (await _redeem(api, second, 1)).status_code == 409
    assert (await _state(api))[2] == links


async def test_generated_invitation_can_be_redeemed_in_existing_path(collab_api):
    api = collab_api
    generated = await api.client.post(
        f"{api.base}/code/generate", headers=api.headers[0], params={"max_uses": 1}
    )
    assert generated.status_code == 201, generated.text
    code = generated.json()["code"]
    assert code.isascii() and all(
        character.isalnum() or character in "_-" for character in code
    )
    redeemed = await _redeem(api, code, 1)
    assert redeemed.status_code == 204, redeemed.text
    assert (await _state(api))[1:] == ([api.users[1]], {})


async def _stored_cover(api):
    cover = f"{api.base}/cover/{uuid4()}"
    async with api.maker() as session:
        trip = await session.get(TripTable, api.trip_key)
        assert trip is not None
        trip.cover = cover
        await session.commit()
    return cover


async def test_owner_deletes_empty_trip_invites_members_and_cover(collab_api):
    api = collab_api
    code = await _link(api, 3)
    assert (await _redeem(api, code, 1)).status_code == 204
    cover = await _stored_cover(api)
    response = await api.client.delete(api.base, headers=api.headers[0])
    assert response.status_code == 204, response.text
    assert await _state(api) == (None, [], {})
    api.storage.remove.assert_called_once_with(cover)
    assert (await api.client.get(api.base, headers=api.headers[0])).status_code == 404
    assert (await _redeem(api, code, 2)).status_code == 409


async def test_locked_mutation_rechecks_revoked_membership(collab_api):
    api = collab_api
    code = await _link(api)
    assert (await _redeem(api, code, 1)).status_code == 204
    async with api.maker() as blocker:
        await blocker.scalar(
            select(TripTable)
            .where(TripTable.primary_key == api.trip_key)
            .with_for_update()
        )
        task = asyncio.create_task(
            api.client.patch(
                api.base, headers=api.headers[1], json={"name": "Revoked edit"}
            )
        )
        try:
            await _wait_for_locks(blocker)
            await blocker.execute(
                delete(CollaboratorTable).where(
                    CollaboratorTable.trip_id == api.trip_key
                )
            )
            await blocker.commit()
            response = await asyncio.wait_for(task, 5)
            assert response.status_code == 404, response.text
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    trip, _, _ = await _state(api)
    assert trip is not None and trip.name == "Shared audit"


async def test_narrowing_trip_dates_cannot_exclude_existing_event(collab_api):
    api = collab_api
    async with api.maker() as session:
        event = EventTable(
            title="Stay",
            type=EventType.STAY,
            trip_id=api.trip_key,
            started_at=datetime(2026, 12, 10, tzinfo=UTC),
            ended_at=datetime(2026, 12, 12, tzinfo=UTC),
        )
        session.add(event)
        await session.commit()
    for changes in ({"startedOn": "2026-12-11"}, {"endedOn": "2026-12-11"}):
        response = await api.client.patch(
            api.base, headers=api.headers[0], json=changes
        )
        assert response.status_code == 422, response.text
    trip, _, _ = await _state(api)
    assert (
        trip is not None
        and trip.started_on == date(2026, 12, 10)
        and trip.ended_on == date(2026, 12, 13)
    )
