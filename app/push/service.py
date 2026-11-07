import json
from base64 import urlsafe_b64encode

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from py_vapid import Vapid, VapidException
from pywebpush import WebPushException, webpush
from requests import RequestException
from requests import Session as RequestsSession
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from app.database import Session
from app.models.push_subscription import (
    CreatePushSubscriptionRequest,
    PushPublicKey,
    PushSubscription,
    PushSubscriptionTable,
)
from app.responses import (
    ConflictError,
    GoneError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.settings import settings


def _load_vapid() -> Vapid:
    key_file = settings.push_vapid_key_file
    if key_file is None or not settings.push_vapid_subject:
        raise ServiceUnavailableError(detail={"reason": "push_not_configured"})
    try:
        key = load_pem_private_key(key_file.read_bytes(), password=None)
    except OSError, ValueError, TypeError, UnsupportedAlgorithm:
        raise ServiceUnavailableError(
            detail={"reason": "push_key_unavailable"}
        ) from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ServiceUnavailableError(detail={"reason": "push_key_unavailable"})
    return Vapid(key)


def get_vapid_public_key() -> PushPublicKey:
    public_key = _load_vapid().public_key.public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return PushPublicKey(public_key=urlsafe_b64encode(public_key).rstrip(b"=").decode())


async def register_subscription(
    session: Session, owner_id: int, request: CreatePushSubscriptionRequest
) -> PushSubscription:
    statement = insert(PushSubscriptionTable).values(
        owner_id=owner_id,
        endpoint=request.endpoint,
        p256dh=request.keys.p256dh,
        auth=request.keys.auth,
    )
    subscription_id = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[PushSubscriptionTable.endpoint],
            set_={"p256dh": statement.excluded.p256dh, "auth": statement.excluded.auth},
            where=PushSubscriptionTable.owner_id == owner_id,
        ).returning(PushSubscriptionTable.id)
    )
    if subscription_id is None:
        raise ConflictError
    await session.commit()
    return PushSubscription(id=subscription_id)


async def delete_subscription(
    session: Session, owner_id: int, subscription_id: int
) -> None:
    deleted = await session.scalar(
        delete(PushSubscriptionTable)
        .where(
            PushSubscriptionTable.owner_id == owner_id,
            PushSubscriptionTable.id == subscription_id,
        )
        .returning(PushSubscriptionTable.id)
    )
    if deleted is None:
        raise NotFoundError
    await session.commit()


def _send_test_push(endpoint: str, p256dh: str, auth: str) -> bool:
    vapid = _load_vapid()
    with RequestsSession() as http:
        http.max_redirects = 0
        try:
            webpush(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {"p256dh": p256dh, "auth": auth},
                },
                data=json.dumps(
                    {
                        "title": "Проверка уведомлений",
                        "body": "Push-уведомления подключены.",
                    },
                    ensure_ascii=False,
                ),
                vapid_private_key=vapid,
                vapid_claims={"sub": settings.push_vapid_subject},
                content_encoding="aes128gcm",
                timeout=5,
                ttl=300,
                requests_session=http,
            )
        except WebPushException as error:
            if error.status_code in (404, 410):
                return False
            raise ServiceUnavailableError from None
        except RequestException, VapidException:
            raise ServiceUnavailableError from None
    return True


async def send_test_notification(
    session: Session, owner_id: int, subscription_id: int
) -> None:
    subscription = await session.scalar(
        select(PushSubscriptionTable).where(
            PushSubscriptionTable.owner_id == owner_id,
            PushSubscriptionTable.id == subscription_id,
        )
    )
    if subscription is None:
        raise NotFoundError

    sent = await run_in_threadpool(
        _send_test_push, subscription.endpoint, subscription.p256dh, subscription.auth
    )
    if sent:
        return

    await session.execute(
        delete(PushSubscriptionTable).where(
            PushSubscriptionTable.primary_key == subscription.primary_key,
            PushSubscriptionTable.p256dh == subscription.p256dh,
            PushSubscriptionTable.auth == subscription.auth,
        )
    )
    await session.commit()
    raise GoneError
