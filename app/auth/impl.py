from logging import getLogger

from fastapi import Response
from sqlalchemy.exc import IntegrityError

from app import users
from app.auth.hasher import check_dummy_hash, check_password, hash_password
from app.auth.tokens import encode_token, send_refresh_token
from app.database import Session
from app.models.auth import AuthToken, AuthTokenResponse
from app.models.user import CreateUserRequest, RegisterUserRequest, User, UserRole
from app.responses import ConflictError, ErrorCode
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
    try:
        user = await users.create_user(
            session,
            CreateUserRequest.model_validate(request),
            hash_password,
        )
    except IntegrityError:
        raise ConflictError(error_code=ErrorCode.USERNAME_IS_ALREADY_IN_USE)

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
            check_dummy_hash()
            raise NotFoundError

        if user.password and check_password(user.password, password):
            return user

        raise NotFoundError

    except NotFoundError:
        raise AuthorizationError


async def login(session: Session, username: str, password: str, response: Response):
    user = await _login_inner(session, username, password)
    auth_token = AuthToken(sub=user.id, role=user.role)
    send_refresh_token(user, response)
    return AuthTokenResponse(access_token=encode_token(auth_token))


__all__ = [
    "create_bootstrap_user",
    "register",
    "login",
    "get_user_by_id",
    "delete_user",
]
