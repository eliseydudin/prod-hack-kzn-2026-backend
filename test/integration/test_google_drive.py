import asyncio
import hashlib
from datetime import timedelta
from io import BytesIO
from os import getenv
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select

from app.auth.tokens import encode_token
from app.database import Session
from app.database.connection import SessionDep, get_session
from app.integrations.google_drive.media import read_photo
from app.integrations.google_drive.middleware import GoogleDriveMiddleware
from app.integrations.google_drive.provider import DRIVE_SCOPE, ProviderError
from app.integrations.google_drive.router import get_drive_service
from app.integrations.google_drive.service import DriveService, digest
from app.main import app
from app.models.auth import AuthToken
from app.models.google_drive import GoogleDriveConnectionTable, GoogleDriveExportTable
from app.models.types.time import utc_now
from app.models.user import UserTable
from app.rate_limit import RateLimiter, create_rate_limit_client, get_rate_limiter
from app.responses import BadRequestError, ConflictError, ServiceUnavailableError
from app.settings import Settings, settings


class FakeDrive:
    def __init__(self):
        self.files = {}
        self.sequence = 0
        self.uploads = 0
        self.time_out_after_upload = False
        self.reject_refresh = False
        self.reject_revoke = False
        self.return_refresh = True
        self.account_id = "account-a"

    async def authorization_url(self, state):
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {"state": state}
        )

    async def exchange(self, code):
        result = {"access_token": "private-access-token", "scope": DRIVE_SCOPE}
        if self.return_refresh:
            result["refresh_token"] = "private-refresh-token"
        return result

    async def account(self, token):
        return {"permissionId": self.account_id, "emailAddress": "drive@example.test"}

    async def refresh(self, token):
        if self.reject_refresh:
            raise ProviderError("google_reconnect_required", reconnect=True)
        assert token == "private-refresh-token"
        return {"access_token": "private-access-token"}

    async def revoke(self, token):
        if self.reject_revoke:
            raise ProviderError("google_unavailable", retryable=True)

    async def generate_ids(self, token, count):
        result = []
        for _ in range(count):
            self.sequence += 1
            result.append(f"file-{self.sequence}")
        return result

    async def get_file(self, token, file_id):
        return self.files.get(file_id)

    async def create_folder(self, token, file_id, owner_id):
        result = {
            "id": file_id,
            "mimeType": "application/vnd.google-apps.folder",
            "appProperties": {"travelhubUserId": owner_id},
        }
        self.files[file_id] = result
        return result

    async def upload_photo(
        self, token, file_id, folder_id, export_id, name, mime_type, data
    ):
        self.uploads += 1
        result = {
            "id": file_id,
            "mimeType": mime_type,
            "size": str(len(data)),
            "md5Checksum": hashlib.md5(data, usedforsecurity=False).hexdigest(),
            "parents": [folder_id],
            "appProperties": {"travelhubExportId": export_id},
        }
        self.files[file_id] = result
        if self.time_out_after_upload:
            raise ProviderError("google_unavailable", retryable=True)
        return result


@pytest.fixture
async def drive_env(monkeypatch):
    config = Settings(
        _env_file=None,
        database_url=settings.database_url,
        jwt_token=settings.jwt_token,
        google_drive_enabled=True,
        google_drive_client_id="test-client",
        google_drive_client_secret="test-secret",
        google_drive_redirect_uri="https://travel.test/api/v1/integrations/google-drive/callback",
        google_drive_token_encryption_key=Fernet.generate_key().decode(),
    )
    for field in type(config).model_fields:
        if field.startswith("google_drive_"):
            monkeypatch.setattr(settings, field, getattr(config, field))
    engine = create_async_engine(settings.database_url.unicode_string())
    provider = FakeDrive()

    async def sessions():
        async with Session(engine, expire_on_commit=False) as session:
            yield session

    async def services(session: SessionDep):
        yield DriveService(session, provider, config)

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_drive_service] = services
    try:
        async with Session(engine, expire_on_commit=False) as session:
            owner = UserTable(username=f"owner{uuid4().hex[:20]}", password="unused")
            other = UserTable(username=f"other{uuid4().hex[:20]}", password="unused")
            session.add_all([owner, other])
            await session.commit()
            service = DriveService(session, provider, config)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://travel.test/api/v1",
                follow_redirects=False,
            ) as client:
                yield SimpleNamespace(
                    session=session,
                    service=service,
                    provider=provider,
                    config=config,
                    owner=UserTable.model_validate(owner),
                    other=UserTable.model_validate(other),
                    client=client,
                    engine=engine,
                )
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_drive_service, None)
        await engine.dispose()


def auth(user):
    return {
        "Authorization": "Bearer "
        + encode_token(AuthToken(sub=user.id, role=user.role))
    }


async def connect_account(env):
    result, cookie = await env.service.begin(env.owner)
    state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
    await env.service.callback(state, cookie, "test-code", False)


async def photo(color="blue"):
    stream = BytesIO()
    Image.new("RGB", (3, 2), color).save(stream, format="PNG")
    return await read_photo(
        UploadFile(BytesIO(stream.getvalue()), filename="photo.png"), 10000
    )


async def test_oauth_state_is_browser_bound_hashed_and_one_use(drive_env):
    env = drive_env
    response = await env.client.post(
        "integrations/google-drive/connect", headers=auth(env.owner)
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    state = parse_qs(urlsplit(response.json()["authorizationUrl"]).query)["state"][0]
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    assert connection.oauth_state_hash == digest(state)
    with pytest.raises(BadRequestError):
        await env.service.callback(state, "wrong-browser", "code", False)
    await env.session.rollback()
    response = await env.client.get(
        "integrations/google-drive/callback", params={"state": state, "code": "code"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?googleDrive=connected"
    assert "private-" not in response.text
    repeat = await env.client.get(
        "integrations/google-drive/callback", params={"state": state, "code": "code"}
    )
    assert repeat.status_code == 400
    status = await env.client.get("integrations/google-drive", headers=auth(env.owner))
    assert status.json()["status"] == "connected"
    assert "token" not in status.text.lower()
    await env.session.refresh(connection)
    assert connection.refresh_token_encrypted != "private-refresh-token"
    assert env.service._decrypt(connection) == "private-refresh-token"


async def test_expired_and_denied_oauth_do_not_connect(drive_env):
    env = drive_env
    result, cookie = await env.service.begin(env.owner)
    state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    connection.oauth_expires_at = utc_now() - timedelta(seconds=1)
    env.session.add(connection)
    await env.session.commit()
    with pytest.raises(BadRequestError):
        await env.service.callback(state, cookie, "code", False)
    result, cookie = await env.service.begin(env.owner)
    state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
    assert await env.service.callback(state, cookie, None, True) == "denied"
    await env.session.refresh(connection)
    assert connection.status == "disconnected"
    assert connection.refresh_token_encrypted is None


async def test_account_change_never_reuses_other_accounts_refresh_token(drive_env):
    env = drive_env
    await connect_account(env)
    env.provider.return_refresh = False
    env.provider.account_id = "account-b"
    result, cookie = await env.service.begin(env.owner)
    state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
    with pytest.raises(ConflictError) as error:
        await env.service.callback(state, cookie, "code", False)
    assert error.value.detail["reason"] == "drive_offline_consent_required"
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    assert connection.permission_id == "account-a"


async def test_reconnect_never_reports_success_with_a_known_invalid_refresh_token(
    drive_env,
):
    env = drive_env
    await connect_account(env)
    env.provider.reject_refresh = True
    with pytest.raises(ConflictError):
        await env.service.export(env.owner, "revoked", await photo())
    env.provider.return_refresh = False
    result, cookie = await env.service.begin(env.owner)
    state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
    with pytest.raises(ConflictError) as error:
        await env.service.callback(state, cookie, "code", False)
    assert error.value.detail["reason"] == "drive_offline_consent_required"
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    assert connection.status == "reconnect_required"


async def test_two_travelhub_accounts_keep_tokens_folders_and_revoke_separate(
    drive_env, monkeypatch
):
    env = drive_env
    refreshed, revoked = [], []

    async def exchange(_code):
        account = env.provider.account_id
        return {
            "access_token": f"access-{account}",
            "refresh_token": f"refresh-{account}",
            "scope": DRIVE_SCOPE,
        }

    async def refresh(token):
        refreshed.append(token)
        return {"access_token": token.replace("refresh-", "access-")}

    async def revoke(token):
        revoked.append(token)

    monkeypatch.setattr(env.provider, "exchange", exchange)
    monkeypatch.setattr(env.provider, "refresh", refresh)
    monkeypatch.setattr(env.provider, "revoke", revoke)
    await connect_account(env)
    env.provider.account_id = "account-b"
    begin, cookie = await env.service.begin(env.other)
    state = parse_qs(urlsplit(begin.authorization_url).query)["state"][0]
    await env.service.callback(state, cookie, "code", False)
    original = await photo()
    first = await env.service.export(env.owner, "same-user-key", original)
    second = await env.service.export(env.other, "same-user-key", original)
    folder_a = env.provider.files[first.drive_file_id]["parents"]
    folder_b = env.provider.files[second.drive_file_id]["parents"]
    assert folder_a != folder_b
    assert refreshed == ["refresh-account-a", "refresh-account-b"]
    env.provider.account_id = "account-c"
    await connect_account(env)
    with pytest.raises(ConflictError) as error:
        await env.service.export(env.owner, "same-user-key", original)
    assert error.value.detail["reason"] == "drive_idempotency_conflict"
    changed = await env.service.export(env.owner, "new-account-key", original)
    assert env.provider.files[changed.drive_file_id]["parents"] not in (
        folder_a,
        folder_b,
    )
    await env.service.disconnect(env.owner)
    assert revoked == ["refresh-account-c"]
    unchanged = await env.service.export(env.other, "same-user-key", original)
    assert unchanged.id == second.id
    assert env.provider.uploads == 3


async def test_scope_loss_stops_export_and_preserves_rotated_token_for_revoke(
    drive_env, monkeypatch
):
    env = drive_env
    await connect_account(env)
    revoked = []

    async def refresh(_token):
        return {
            "access_token": "private-access-token",
            "refresh_token": "rotated-private-refresh-token",
            "scope": "",
        }

    async def revoke(token):
        revoked.append(token)

    monkeypatch.setattr(env.provider, "refresh", refresh)
    monkeypatch.setattr(env.provider, "revoke", revoke)
    with pytest.raises(ConflictError) as error:
        await env.service.export(env.owner, "empty-scope", await photo())
    assert error.value.detail["reason"] == "drive_scope_not_granted"
    assert not env.provider.files
    await env.service.disconnect(env.owner)
    assert revoked == ["rotated-private-refresh-token"]


async def test_photo_export_is_owner_scoped_and_idempotent(drive_env):
    env = drive_env
    await connect_account(env)
    original = await photo()
    headers = {**auth(env.owner), "Idempotency-Key": "export-one"}
    response = await env.client.post(
        "integrations/google-drive/photo-exports",
        headers=headers,
        files={"file": (original.name, original.data, "image/png")},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "succeeded"
    assert isinstance(result["id"], str)
    repeat = await env.client.post(
        "integrations/google-drive/photo-exports",
        headers=headers,
        files={"file": (original.name, original.data, "image/png")},
    )
    assert repeat.json()["id"] == result["id"]
    assert env.provider.uploads == 1
    assert len(env.provider.files) == 2
    foreign = await env.client.get(
        f"integrations/google-drive/photo-exports/{result['id']}",
        headers=auth(env.other),
    )
    assert foreign.status_code == 404
    foreign_upload = await env.client.post(
        "integrations/google-drive/photo-exports",
        headers={**auth(env.other), "Idempotency-Key": "export-one"},
        files={"file": (original.name, original.data, original.mime_type)},
    )
    assert foreign_upload.status_code == 409
    assert foreign_upload.json()["detail"]["reason"] == "drive_not_connected"
    assert env.provider.uploads == 1
    changed = await photo("red")
    response = await env.client.post(
        "integrations/google-drive/photo-exports",
        headers=headers,
        files={"file": (changed.name, changed.data, "image/png")},
    )
    assert response.status_code == 409
    assert env.provider.uploads == 1


async def test_uncertain_upload_is_reconciled_after_new_session_without_duplicate(
    drive_env,
):
    env = drive_env
    await connect_account(env)
    original = await photo()
    env.provider.time_out_after_upload = True
    with pytest.raises(ServiceUnavailableError):
        await env.service.export(env.owner, "timeout", original)
    operation = await env.session.scalar(
        select(GoogleDriveExportTable).where(
            GoogleDriveExportTable.user_id == env.owner.primary_key
        )
    )
    assert operation.status == "uncertain"
    file_id = operation.drive_file_id
    await env.session.rollback()
    async with Session(env.engine, expire_on_commit=False) as session:
        recovered = await DriveService(session, env.provider, env.config).export(
            env.owner, "timeout", original
        )
    assert recovered.status == "succeeded"
    assert env.provider.uploads == 1
    assert recovered.drive_file_id == file_id


async def test_restart_after_reservation_uses_persisted_ids(drive_env, monkeypatch):
    env = drive_env
    await connect_account(env)
    original = await photo()
    original_lock = env.service._lock
    calls = 0

    async def interrupted_lock(user_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError()
        return await original_lock(user_id, **kwargs)

    monkeypatch.setattr(env.service, "_lock", interrupted_lock)
    with pytest.raises(asyncio.CancelledError):
        await env.service.export(env.owner, "reserved-before-restart", original)
    operation = await env.session.scalar(
        select(GoogleDriveExportTable).where(
            GoogleDriveExportTable.user_id == env.owner.primary_key
        )
    )
    reserved_file_id = operation.drive_file_id
    assert operation.status == "pending"
    assert env.provider.sequence == 2
    assert env.provider.uploads == 0
    await env.session.rollback()
    async with Session(env.engine, expire_on_commit=False) as session:
        recovered = await DriveService(session, env.provider, env.config).export(
            env.owner, "reserved-before-restart", original
        )
    assert recovered.drive_file_id == reserved_file_id
    assert recovered.status == "succeeded"
    assert env.provider.sequence == 2
    assert env.provider.uploads == 1


async def test_existing_file_with_wrong_fingerprint_is_not_accepted(drive_env):
    env = drive_env
    await connect_account(env)
    original = await photo()
    env.provider.time_out_after_upload = True
    with pytest.raises(ServiceUnavailableError):
        await env.service.export(env.owner, "mismatch", original)
    operation = await env.session.scalar(
        select(GoogleDriveExportTable).where(
            GoogleDriveExportTable.user_id == env.owner.primary_key
        )
    )
    env.provider.files[operation.drive_file_id]["md5Checksum"] = "wrong-hash"
    with pytest.raises(ConflictError):
        await env.service.export(env.owner, "mismatch", original)
    await env.session.refresh(operation)
    assert operation.status == "failed"


async def test_expired_google_grant_requires_reconnect(drive_env):
    env = drive_env
    await connect_account(env)
    env.provider.reject_refresh = True
    with pytest.raises(ConflictError):
        await env.service.export(env.owner, "refresh", await photo())
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    assert connection.status == "reconnect_required"
    assert env.provider.uploads == 0


async def test_disconnect_stops_work_even_when_google_revoke_fails(drive_env):
    env = drive_env
    await connect_account(env)
    env.provider.reject_revoke = True
    result = await env.service.disconnect(env.owner)
    assert result.status == "disconnected"
    assert result.revocation_pending
    with pytest.raises(ConflictError):
        await env.service.export(env.owner, "disconnected", await photo())
    with pytest.raises(ConflictError):
        await env.service.begin(env.owner)
    env.provider.reject_revoke = False
    result = await env.service.disconnect(env.owner)
    assert not result.revocation_pending
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    assert connection.refresh_token_encrypted is None


async def test_disconnect_does_not_clear_a_new_connection(drive_env, monkeypatch):
    env = drive_env
    commit = env.session.commit

    async def commit_then_reconnect():
        monkeypatch.setattr(env.session, "commit", commit)
        await commit()
        async with Session(env.engine, expire_on_commit=False) as session:
            service = DriveService(session, env.provider, env.config)
            result, cookie = await service.begin(env.owner)
            state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
            await service.callback(state, cookie, "test-code", False)

    monkeypatch.setattr(env.session, "commit", commit_then_reconnect)
    result = await env.service.disconnect(env.owner)
    assert result.status == "disconnected"
    status = await env.client.get("integrations/google-drive", headers=auth(env.owner))
    assert status.json()["status"] == "connected"
    assert status.json()["email"] == "drive@example.test"


async def test_second_process_cannot_refresh_or_disconnect_under_owner_lock(drive_env):
    env = drive_env
    await connect_account(env)
    await env.service._lock(env.owner.primary_key)
    async with Session(env.engine, expire_on_commit=False) as session:
        other_process = DriveService(session, env.provider, env.config)
        with pytest.raises(ConflictError) as error:
            await other_process.disconnect(env.owner)
        assert error.value.detail["reason"] == "drive_operation_in_progress"


async def test_parallel_http_exports_and_disconnect_cannot_duplicate_upload(
    drive_env, monkeypatch
):
    env = drive_env
    await connect_account(env)
    original = await photo()
    upload_started, continue_upload = asyncio.Event(), asyncio.Event()
    original_upload = env.provider.upload_photo

    async def upload(*args):
        upload_started.set()
        await continue_upload.wait()
        return await original_upload(*args)

    monkeypatch.setattr(env.provider, "upload_photo", upload)
    headers = {**auth(env.owner), "Idempotency-Key": "parallel-http"}
    request = {
        "headers": headers,
        "files": {"file": (original.name, original.data, original.mime_type)},
    }
    first = asyncio.create_task(
        env.client.post("integrations/google-drive/photo-exports", **request)
    )
    try:
        await asyncio.wait_for(upload_started.wait(), timeout=3)
        repeated = await env.client.post(
            "integrations/google-drive/photo-exports", **request
        )
        disconnect = await env.client.delete(
            "integrations/google-drive", headers=auth(env.owner)
        )
        independent = await env.client.get(
            "integrations/google-drive", headers=auth(env.other)
        )
        for response in (repeated, disconnect):
            assert response.status_code == 409
            assert response.json()["detail"]["reason"] == "drive_operation_in_progress"
        assert independent.status_code == 200
    finally:
        continue_upload.set()
        response = await first
    assert response.status_code == 201, response.text
    repeated = await env.client.post(
        "integrations/google-drive/photo-exports", **request
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == response.json()["id"]
    assert env.provider.uploads == 1


async def test_disconnect_during_callback_cannot_restore_old_connection(
    drive_env, monkeypatch
):
    env = drive_env
    await connect_account(env)
    begin = await env.client.post(
        "integrations/google-drive/connect", headers=auth(env.owner)
    )
    state = parse_qs(urlsplit(begin.json()["authorizationUrl"]).query)["state"][0]
    exchange_started, continue_exchange = asyncio.Event(), asyncio.Event()
    original_exchange = env.provider.exchange

    async def exchange(code):
        exchange_started.set()
        await continue_exchange.wait()
        return await original_exchange(code)

    monkeypatch.setattr(env.provider, "exchange", exchange)
    callback = asyncio.create_task(
        env.client.get(
            "integrations/google-drive/callback",
            params={"state": state, "code": "private-code"},
        )
    )
    try:
        await asyncio.wait_for(exchange_started.wait(), timeout=3)
        disconnect = await env.client.delete(
            "integrations/google-drive", headers=auth(env.owner)
        )
        assert disconnect.status_code == 200
    finally:
        continue_exchange.set()
        response = await callback
    assert response.status_code == 409
    status = await env.client.get("integrations/google-drive", headers=auth(env.owner))
    assert status.json()["status"] == "disconnected"
    connection = await env.session.scalar(
        select(GoogleDriveConnectionTable).where(
            GoogleDriveConnectionTable.user_id == env.owner.primary_key
        )
    )
    assert connection.refresh_token_encrypted is None


async def test_deleted_travelhub_account_cannot_use_drive_or_complete_oauth(
    drive_env, monkeypatch
):
    env = drive_env
    await connect_account(env)
    begin = await env.client.post(
        "integrations/google-drive/connect", headers=auth(env.owner)
    )
    state = parse_qs(urlsplit(begin.json()["authorizationUrl"]).query)["state"][0]

    async def exchange(_code):
        pytest.fail("A disabled TravelHub account must not exchange an OAuth code")

    monkeypatch.setattr(env.provider, "exchange", exchange)
    deleted = await env.client.delete("auth/me", headers=auth(env.owner))
    assert deleted.status_code == 204
    for method, path in [
        ("GET", "integrations/google-drive"),
        ("POST", "integrations/google-drive/connect"),
        ("DELETE", "integrations/google-drive"),
        ("GET", "integrations/google-drive/photo-exports/1"),
    ]:
        response = await env.client.request(method, path, headers=auth(env.owner))
        assert response.status_code == 401
    export = await env.client.post(
        "integrations/google-drive/photo-exports",
        headers={**auth(env.owner), "Idempotency-Key": "after-delete"},
        files={"file": ("photo.png", b"unused", "image/png")},
    )
    assert export.status_code == 401
    callback = await env.client.get(
        "integrations/google-drive/callback",
        params={"state": state, "code": "private-code"},
    )
    assert callback.status_code == 401


async def test_callback_validation_never_echoes_oauth_secrets(drive_env):
    response = await drive_env.client.get(
        "integrations/google-drive/callback",
        params={"state": "private-state", "code": "private-code" * 400},
    )
    assert response.status_code == 422
    assert "private-" not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_disabled_feature_blocks_google_work_and_keeps_owned_history(
    drive_env, monkeypatch
):
    env = drive_env
    await connect_account(env)
    operation = await env.service.export(env.owner, "before-disabled", await photo())
    monkeypatch.setattr(settings, "google_drive_enabled", False)
    app.dependency_overrides.pop(get_drive_service)
    status = await env.client.get("integrations/google-drive", headers=auth(env.owner))
    assert status.json()["status"] == "disabled"
    assert not status.json()["enabled"]
    for method, path, kwargs in [
        ("POST", "integrations/google-drive/connect", {}),
        ("DELETE", "integrations/google-drive", {}),
        ("GET", "integrations/google-drive/callback", {"params": {"state": "x" * 32}}),
        (
            "POST",
            "integrations/google-drive/photo-exports",
            {"files": {"file": ("photo.png", b"unused", "image/png")}},
        ),
    ]:
        response = await env.client.request(
            method, path, headers=auth(env.owner), **kwargs
        )
        assert response.status_code == 503
        assert response.json()["detail"]["reason"] == "google_drive_disabled"
    path = f"integrations/google-drive/photo-exports/{operation.id}"
    owned = await env.client.get(path, headers=auth(env.owner))
    foreign = await env.client.get(path, headers=auth(env.other))
    assert owned.status_code == 200
    assert foreign.status_code == 404


async def test_deleted_drive_folder_can_be_recovered_by_disconnect_and_reconnect(
    drive_env,
):
    env = drive_env
    await connect_account(env)
    original = await photo()
    first = await env.service.export(env.owner, "before-trash", original)
    folder = env.provider.files[first.drive_file_id]["parents"][0]
    env.provider.files[folder]["trashed"] = True
    with pytest.raises(ConflictError) as error:
        await env.service.export(env.owner, "trashed", original)
    assert error.value.detail["reason"] == "drive_folder_unavailable"
    await env.service.disconnect(env.owner)
    await connect_account(env)
    recovered = await env.service.export(env.owner, "after-reconnect", original)
    assert recovered.status == "succeeded"
    assert env.provider.files[recovered.drive_file_id]["parents"] != [folder]


async def test_export_rechecks_connection_after_persisting_reserved_id(
    drive_env, monkeypatch
):
    env = drive_env
    await connect_account(env)
    original_lock = env.service._lock
    calls = 0

    async def changed_connection(user_id, **kwargs):
        nonlocal calls
        calls += 1
        connection = await original_lock(user_id, **kwargs)
        if calls == 2:
            connection.generation += 1
        return connection

    monkeypatch.setattr(env.service, "_lock", changed_connection)
    with pytest.raises(ConflictError):
        await env.service.export(env.owner, "changed-account", await photo())
    assert env.provider.uploads == 0


async def test_chunked_upload_is_bounded_before_multipart_buffering(drive_env):
    async def consume(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    config = drive_env.config.model_copy(update={"google_drive_max_photo_bytes": 100})
    middleware = GoogleDriveMiddleware(consume, config)

    async def chunks():
        for _ in range(70):
            yield b"a" * 1024

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="https://test"
    ) as client:
        response = await client.post(
            "/integrations/google-drive/photo-exports", content=chunks()
        )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"


async def test_real_multipart_parser_keeps_chunked_limit_response_413(drive_env):
    async def chunks():
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.png"\r\nContent-Type: image/png\r\n\r\n'
        for _ in range(11):
            yield b"x" * 1_000_000
        yield b"\r\n--boundary--\r\n"

    response = await drive_env.client.post(
        "integrations/google-drive/photo-exports",
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        content=chunks(),
    )
    assert response.status_code == 413
    assert response.json()["detail"]["reason"] == "photo_too_large"


async def test_drive_uses_shared_user_quota_without_blocking_oauth_callback(
    drive_env, monkeypatch
):
    url = getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL must point to a test Valkey instance")
    env = drive_env
    result, cookie = await env.service.begin(env.owner)
    state = parse_qs(urlsplit(result.authorization_url).query)["state"][0]
    env.client.cookies.set(
        "travelhub_drive_oauth", cookie, domain="travel.test", path="/"
    )
    monkeypatch.setattr(settings, "rate_limit_user", 1)
    async with create_rate_limit_client(url) as client:
        limiter = RateLimiter(client, 60)
        app.dependency_overrides[get_rate_limiter] = lambda: limiter
        try:
            first = await env.client.get(
                "integrations/google-drive", headers=auth(env.owner)
            )
            assert first.status_code == 200
            rejected = await env.client.get("trips/", headers=auth(env.owner))
            assert rejected.status_code == 429
            assert int(rejected.headers["Retry-After"]) > 0
            other = await env.client.get(
                "integrations/google-drive", headers=auth(env.other)
            )
            assert other.status_code == 200
            callback = await env.client.get(
                "integrations/google-drive/callback",
                params={"state": state, "code": "code"},
            )
            assert callback.status_code == 303
        finally:
            app.dependency_overrides.pop(get_rate_limiter, None)
