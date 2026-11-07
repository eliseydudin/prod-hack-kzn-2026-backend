from typing import Annotated

from fastapi import APIRouter, Path, status

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
    get_vapid_public_key,
    register_subscription,
    send_test_notification,
)
from app.responses import (
    AuthorizationError,
    ConflictError,
    GoneError,
    NotFoundError,
    ServiceUnavailableError,
    responses,
)

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
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(
        AuthorizationError, NotFoundError, GoneError, ServiceUnavailableError
    ),
)
async def post_test_notification(
    request: TestPushRequest, session: SessionDep, user: CurrentUser
) -> None:
    await send_test_notification(session, user.primary_key, request.subscription_id)
