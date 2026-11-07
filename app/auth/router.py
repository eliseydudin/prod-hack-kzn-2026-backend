from logging import getLogger
from typing import Annotated

from fastapi import Depends, Response
from fastapi.routing import APIRouter
from fastapi.security.oauth2 import OAuth2PasswordRequestFormStrict

from app.auth import impl
from app.auth.tokens import (
    CurrentUser,
    RefreshTokenDep,
    delete_refresh_token,
    encode_token,
)
from app.database import SessionDep
from app.models.auth import AuthToken, AuthTokenResponse
from app.models.user import (
    CreateUserRequest,
    RegisterUserRequest,
    User,
)
from app.rate_limit import limit_login, limit_refresh, limit_registration
from app.responses import (
    AuthorizationError,
    ConflictError,
    RateLimitError,
    ServiceUnavailableError,
    responses,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = getLogger(__name__)


@router.post(
    "/register",
    dependencies=[Depends(limit_registration)],
    responses=responses(ConflictError, RateLimitError, ServiceUnavailableError),
    status_code=201,
)
async def register(session: SessionDep, req: RegisterUserRequest) -> User:
    return await impl.register(session, CreateUserRequest.model_validate(req))


@router.post(
    "/login",
    dependencies=[Depends(limit_login)],
    responses=responses(AuthorizationError, RateLimitError, ServiceUnavailableError),
    status_code=200,
)
async def login(
    session: SessionDep,
    credentials: Annotated[OAuth2PasswordRequestFormStrict, Depends()],
    response: Response,
) -> AuthTokenResponse:
    return await impl.login(
        session,
        credentials.username,
        credentials.password,
        response,
    )


@router.post(
    "/refresh",
    dependencies=[Depends(limit_refresh)],
    responses=responses(AuthorizationError, RateLimitError),
    status_code=200,
)
async def refresh_token(
    session: SessionDep,
    refresh_token: RefreshTokenDep,
) -> AuthTokenResponse:
    user_table = await impl.get_user_by_id(session, refresh_token.sub)
    if not user_table:
        raise AuthorizationError
    auth_token = AuthToken(sub=user_table.id, role=user_table.role)
    return AuthTokenResponse(access_token=encode_token(auth_token))


@router.post(
    "/logout",
    status_code=204,
    responses=responses(AuthorizationError),
    description="Deletes the refresh token",
)
async def logout(
    _user: CurrentUser,
    response: Response,
):
    delete_refresh_token(response)


@router.get("/me", responses=responses(AuthorizationError), status_code=200)
async def get_self(user: CurrentUser) -> User:
    return User.model_validate(user)


@router.delete(
    "/me",
    responses=responses(AuthorizationError, ConflictError),
    status_code=204,
)
async def delete_self(
    me: CurrentUser,
    session: SessionDep,
) -> None:
    await impl.delete_user(session, me)
    logger.info(
        "User @%s (%s) deleted their account",
        me.username,
        me.id,
    )
