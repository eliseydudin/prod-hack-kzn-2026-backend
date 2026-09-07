from datetime import timedelta

from app.api.impl.user import create_user
from app.database import get_session_ctx
from app.models.types.time import utc_now
from app.models.user import CreateUserRequest, UserRole
from app.utils.hasher import check_password


async def test_create_user():
    request = CreateUserRequest(
        username="testuser",
        password="password",
        role=UserRole.ADMIN,
    )

    async with get_session_ctx() as ctx:
        user = await create_user(ctx, request)

    assert user.username == "testuser"
    assert check_password(user.password or "", "password")
    assert user.role == UserRole.ADMIN

    assert user.updated_at == user.created_at
    assert utc_now() - user.created_at <= timedelta(seconds=0.1)
