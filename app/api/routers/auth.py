from logging import getLogger
from typing import Annotated, Optional

from fastapi import Depends, Query, Response
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRouter
from fastapi.security.oauth2 import OAuth2PasswordRequestFormStrict

from app.api.auth import CurrentUser, RefreshTokenDep, encode_token, send_refresh_token
from app.api.google import profile as google
from app.api.impl import user
from app.database import SessionDep
from app.models.auth import AuthToken, AuthTokenResponse
from app.models.user import CreateUserRequest, RegisterUserRequest, User, UserMeta
from app.responses import ConflictError, responses
from app.responses.exceptions import AuthorizationError
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])
logger = getLogger(__name__)


@router.post("/register", responses=responses(ConflictError), status_code=201)
async def register(session: SessionDep, req: RegisterUserRequest) -> User:
    user_table = await user.create_user(session, CreateUserRequest.model_validate(req))
    logger.info(
        "User @%s (%s) registered their account",
        user_table.username,
        user_table.id,
    )
    return User.model_validate(user_table)


@router.post("/login", responses=responses(AuthorizationError), status_code=200)
async def login(
    session: SessionDep,
    credentials: Annotated[OAuth2PasswordRequestFormStrict, Depends()],
    response: Response,
) -> AuthTokenResponse:
    user_table = await user.login_user(
        session,
        credentials.username,
        credentials.password,
    )
    auth_token = AuthToken(sub=user_table.id, role=user_table.role)
    send_refresh_token(user_table, response)
    return AuthTokenResponse(access_token=encode_token(auth_token))


@router.post("/refresh", status_code=200, responses=responses(AuthorizationError))
async def refresh_token(
    session: SessionDep,
    refresh_token: RefreshTokenDep,
) -> AuthTokenResponse:
    user_table = await user.get_user_by_id(session, refresh_token.sub)
    auth_token = AuthToken(sub=user_table.id, role=user_table.role)
    return AuthTokenResponse(access_token=encode_token(auth_token))


@router.get("/meta", responses=responses(AuthorizationError), status_code=200)
async def who_am_i(
    refresh_token: RefreshTokenDep,
) -> UserMeta:
    return UserMeta(refresh_token_user_id=refresh_token.sub)


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
    hard: Optional[bool] = Query(
        None, description="If true delete the user permanently"
    ),
) -> None:
    await user.delete_user(session, me, bool(hard))
    logger.info(
        "User @%s (%s) just deleted their account (permanently=%s)",
        me.username,
        me.id,
        "true" if hard else "false",
    )


google_router = APIRouter(prefix="/google", tags=["google"], include_in_schema=False)


@google_router.get("/", status_code=307)
async def google_auth():
    redirect_url = await google.google_api.get_authorization_url(
        settings.google_callback_url
    )
    return RedirectResponse(redirect_url)


@google_router.get("/callback", status_code=200)
async def google_auth_callback(
    session: SessionDep, profile: google.GoogleProfileDep, response: Response
) -> User:
    created, new_user = await user.get_user_from_google_profile(session, profile)
    if created:
        logger.info(
            "Created a new account @%s (%s) by using Google's social login (%s)",
            new_user.username,
            new_user.id,
            profile.email,
        )

    send_refresh_token(new_user, response)
    return User.model_validate(new_user)


router.include_router(google_router)
