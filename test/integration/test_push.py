import json
from base64 import urlsafe_b64encode
from os import urandom
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from http_ece import decrypt
from httpx import ASGITransport, AsyncClient
from kombu.exceptions import OperationalError
from pydantic import RedisDsn
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.tokens import get_current_user
from app.database import Session, get_session
from app.main import app
from app.models.push_subscription import CreatePushSubscriptionRequest
from app.models.user import UserTable
from app.push.service import (
    delete_subscription,
    get_vapid_public_key,
    register_subscription,
    send_test_notification,
)
from app.rate_limit import get_rate_limiter
from app.responses import (
    ConflictError,
    GoneError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.settings import settings


@pytest.fixture
async def push_session():
    engine = create_async_engine(settings.database_url.unicode_string())
    try:
        async with engine.connect() as connection:
            await connection.begin()
            async with Session(
                connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session:
                yield session
    finally:
        await engine.dispose()


def _public_key(key: ec.EllipticCurvePrivateKey) -> str:
    public = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return urlsafe_b64encode(public).rstrip(b"=").decode()


async def test_push_subscription_lifecycle(
    push_session: Session, monkeypatch, tmp_path
):
    vapid_key = ec.generate_private_key(ec.SECP256R1())
    key_file = tmp_path / "private_key.pem"
    key_file.write_bytes(
        vapid_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    monkeypatch.setattr(settings, "push_vapid_key_file", key_file)
    monkeypatch.setattr(settings, "push_vapid_subject", "mailto:push@example.com")
    public_key = get_vapid_public_key().public_key
    assert public_key == _public_key(vapid_key)

    owner = UserTable(username=f"push{uuid4().hex[:24]}", password=None)
    other = UserTable(username=f"push{uuid4().hex[:24]}", password=None)
    push_session.add_all([owner, other])
    await push_session.commit()
    owner_id, other_id = owner.primary_key, other.primary_key

    browser_key = ec.generate_private_key(ec.SECP256R1())
    browser_secret = urandom(16)
    request = CreatePushSubscriptionRequest.model_validate(
        {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-browser",
            "keys": {
                "p256dh": _public_key(browser_key),
                "auth": urlsafe_b64encode(browser_secret).rstrip(b"=").decode(),
            },
        }
    )
    subscription = await register_subscription(push_session, owner_id, request)
    assert await register_subscription(push_session, owner_id, request) == subscription
    with pytest.raises(ConflictError):
        await register_subscription(push_session, other_id, request)
    with pytest.raises(NotFoundError):
        await delete_subscription(push_session, other_id, subscription.id)

    deliveries: list[PreparedRequest] = []
    provider_status = 201

    def provider(_adapter, prepared: PreparedRequest, **_kwargs) -> Response:
        deliveries.append(prepared)
        response = Response()
        response.status_code = provider_status
        response.request = prepared
        response.url = prepared.url
        response._content = b""
        response._content_consumed = True
        if provider_status == 307:
            response.headers["Location"] = "https://127.0.0.1/internal"
        return response

    monkeypatch.setattr(HTTPAdapter, "send", provider)
    with pytest.raises(NotFoundError):
        await send_test_notification(push_session, other_id, subscription.id)
    assert deliveries == []

    await send_test_notification(push_session, owner_id, subscription.id)
    (delivery,) = deliveries
    assert delivery.headers["Content-Encoding"] == "aes128gcm"
    assert delivery.headers["Authorization"].endswith(f",k={public_key}")
    decoded = decrypt(
        delivery.body, private_key=browser_key, auth_secret=browser_secret
    )
    assert json.loads(decoded) == {
        "title": "Проверка уведомлений",
        "body": "Push-уведомления подключены.",
    }

    provider_status = 307
    with pytest.raises(ServiceUnavailableError):
        await send_test_notification(push_session, owner_id, subscription.id)
    assert [delivery.url for delivery in deliveries] == [request.endpoint] * 2

    provider_status = 410
    with pytest.raises(GoneError):
        await send_test_notification(push_session, owner_id, subscription.id)
    with pytest.raises(NotFoundError):
        await send_test_notification(push_session, owner_id, subscription.id)

    registered = await register_subscription(push_session, owner_id, request)
    await delete_subscription(push_session, owner_id, registered.id)
    with pytest.raises(NotFoundError):
        await send_test_notification(push_session, owner_id, registered.id)


async def test_push_endpoint_queues_only_owned_subscriptions(monkeypatch):
    user = UserTable(primary_key=1, username="push-owner")
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    monkeypatch.setitem(app.dependency_overrides, get_session, lambda: None)
    monkeypatch.setitem(app.dependency_overrides, get_rate_limiter, lambda: None)
    monkeypatch.setattr(settings, "redis_url", RedisDsn("redis://localhost:6379/0"))
    monkeypatch.setattr("app.push.router.get_vapid_public_key", Mock())
    lookup = AsyncMock(side_effect=NotFoundError)
    monkeypatch.setattr("app.push.router.get_subscription", lookup)
    publish = Mock()
    monkeypatch.setattr(
        "app.push.worker.deliver_test_notification.apply_async", publish
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        url = "/api/v1/push/test"
        request = {"subscriptionId": "42"}
        assert (await client.post(url, json=request)).status_code == 404
        lookup.assert_awaited_once_with(None, 1, 42)
        publish.assert_not_called()

        lookup.side_effect = None
        response = await client.post(url, json=request)
        assert response.status_code == 202
        assert not response.content
        publish.assert_called_once_with(args=(1, 42), expires=300, retry=False)

        publish.side_effect = OperationalError
        response = await client.post(url, json=request)
        assert response.status_code == 503
        assert response.json()["detail"]["reason"] == "push_queue_unavailable"
