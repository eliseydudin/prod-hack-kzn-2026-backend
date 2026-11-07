from uuid import uuid4

import pytest

from app.auth.hasher import check_password, hash_password
from app.database import get_session_ctx
from app.models.user import CreateUserRequest, UserRole
from app.responses import ConflictError, ErrorCode
from app.users import create_user


async def test_create_user():
    request = CreateUserRequest(
        username=f"testuser.{uuid4().hex[:16]}",
        password="password",
        role=UserRole.ADMIN,
    )

    async with get_session_ctx() as ctx:
        user = await create_user(ctx, request, hash_password)

    assert user.username == request.username
    assert check_password(user.password or "", "password")
    assert user.role == UserRole.ADMIN

    assert user.updated_at == user.created_at

    async with get_session_ctx() as ctx:
        with pytest.raises(ConflictError) as conflict:
            await create_user(ctx, request, hash_password)
    assert conflict.value.error_code == ErrorCode.USERNAME_IS_ALREADY_IN_USE
