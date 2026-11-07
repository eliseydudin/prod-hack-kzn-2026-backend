from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from app.auth.tokens import CurrentUser
from app.database import SessionDep
from app.models.push_subscription import (
    CreatePushSubscriptionRequest,
    PushPublicKey,
    PushSubscription,
    TestPushRequest,
)
from app.models.types.snowflake_id import SnowflakeId
from app.push.service import (
    delete_subscription,
    get_subscription,
    get_vapid_public_key,
    register_subscription,
)
from app.push.worker import deliver_test_notification
from app.rate_limit import limit_user_requests
from app.responses import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    responses,
)
from app.settings import settings

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", responses=responses(ServiceUnavailableError))
def get_public_key() -> PushPublicKey:
    return get_vapid_public_key()


@router.post("/subscriptions", responses=responses(AuthorizationError, ConflictError))
async def post_subscription(
    request: CreatePushSubscriptionRequest, session: SessionDep, user: CurrentUser
) -> PushSubscription:
    return await register_subscription(session, user.primary_key, request)


@router.delete(
    "/subscriptions/{subscriptionId}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError),
)
async def remove_subscription(
    subscription_id: Annotated[SnowflakeId, Path(alias="subscriptionId", gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> None:
    await delete_subscription(session, user.primary_key, subscription_id)


@router.post(
    "/test",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    dependencies=[Depends(limit_user_requests)],
    responses=responses(
        AuthorizationError, NotFoundError, RateLimitError, ServiceUnavailableError
    ),
)
async def post_test_notification(
    request: TestPushRequest, session: SessionDep, user: CurrentUser
) -> None:
    await get_subscription(session, user.primary_key, request.subscription_id)
    if settings.redis_url is None:
        raise ServiceUnavailableError(detail={"reason": "push_queue_unavailable"})
    await run_in_threadpool(get_vapid_public_key)
    try:
        await run_in_threadpool(
            deliver_test_notification.apply_async,
            args=(user.primary_key, request.subscription_id),
            expires=300,
            retry=False,
        )
    except OperationalError, RedisError:
        raise ServiceUnavailableError(
            detail={"reason": "push_queue_unavailable"}
        ) from None
