from logging import getLogger

from fastapi import Response
from starlette.concurrency import run_in_threadpool

from app import users
from app.auth.hasher import check_dummy_hash, check_password, hash_password
from app.auth.tokens import encode_token, send_refresh_token
from app.database import Session
from app.models.auth import AuthToken, AuthTokenResponse
from app.models.user import (
    ChangePasswordRequest,
    CreateUserRequest,
    RegisterUserRequest,
    User,
    UserRole,
    UserTable,
)
from app.responses import ConflictError, ForbiddenError
from app.responses.exceptions.http import AuthorizationError, NotFoundError
from app.settings import settings
from app.users import delete_user, get_user_by_id

logger = getLogger(__name__)


async def create_bootstrap_user(session: Session):
    if not settings.bootstrap_username or not settings.bootstrap_password:
        return

    username = settings.bootstrap_username
    password = settings.bootstrap_password

    try:
        if await users.get_bootstrap_user(session) is not None:
            raise ConflictError(
                detail={"message": "attempted to create a new bootstrap user"}
            )

        request = CreateUserRequest(
            username=username,
            password=password,
            role=UserRole.BOOTSTRAP,
        )

        await users.create_user(session, request, hash_password)
        logger.info("Created a bootstrap user '%s'", username)
    except ConflictError as e:
        logger.warning("Consider removing bootstrap credentials from env", exc_info=e)


async def register(session: Session, request: RegisterUserRequest):
    user = await users.create_user(
        session,
        CreateUserRequest.model_validate(request),
        hash_password,
    )

    logger.info(
        "User @%s (%s) registered their account",
        user.username,
        user.id,
    )

    return User.model_validate(user)


async def _login_inner(session: Session, username: str, password: str):
    try:
        user = await users.get_user_by_username(session, username)
        if not user:
            await run_in_threadpool(check_dummy_hash)
            raise NotFoundError

        if user.password and await run_in_threadpool(
            check_password, user.password, password
        ):
            return user

        raise NotFoundError

    except NotFoundError:
        raise AuthorizationError


async def login(session: Session, username: str, password: str, response: Response):
    user = await _login_inner(session, username, password)
    auth_token = AuthToken(sub=user.id, role=user.role)
    send_refresh_token(user, response)
    return AuthTokenResponse(access_token=encode_token(auth_token))


async def change_password(
    session: Session,
    user: UserTable,
    request: ChangePasswordRequest,
) -> None:
    await session.refresh(user, with_for_update=True)
    if not user.enabled:
        raise AuthorizationError()
    if not user.password:
        raise ForbiddenError(detail={"reason": "password_unavailable"})
    if not await run_in_threadpool(
        check_password, user.password, request.current_password
    ):
        raise ForbiddenError(detail={"reason": "invalid_current_password"})

    user.password = await run_in_threadpool(hash_password, request.new_password)
    await users.add_or_update_user(session, user)


__all__ = [
    "create_bootstrap_user",
    "register",
    "login",
    "change_password",
    "get_user_by_id",
    "delete_user",
]
