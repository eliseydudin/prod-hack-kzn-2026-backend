from typing import Annotated, Optional

from fastapi import Depends, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRouter
from fastapi.security.oauth2 import OAuth2PasswordRequestFormStrict

from app.api.auth import CurrentUser, encode_token
from app.api.google import profile as google
from app.api.impl import user
from app.database import SessionDep
from app.models.auth import AuthToken, AuthTokenResponse
from app.models.user import CreateUserRequest, RegisterUserRequest, User
from app.responses import ConflictError, responses
from app.responses.exceptions import AuthorizationError
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", responses=responses(ConflictError), status_code=201)
async def register(session: SessionDep, req: RegisterUserRequest) -> User:
    user_table = await user.create_user(session, CreateUserRequest.model_validate(req))
    return User.model_validate(user_table)


@router.post("/login", responses=responses(AuthorizationError), status_code=200)
async def login(
    session: SessionDep,
    credentials: Annotated[OAuth2PasswordRequestFormStrict, Depends()],
) -> AuthTokenResponse:
    user_table = await user.login_user(
        session, credentials.username, credentials.password
    )
    auth_token = AuthToken(sub=user_table.id, role=user_table.role)
    return AuthTokenResponse(access_token=encode_token(auth_token))


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


google_router = APIRouter(prefix="/google", tags=["google"], include_in_schema=False)


@google_router.get("/", status_code=307, tags=["google"])
async def google_auth(request: Request):
    redirect_url = await google.google_api.get_authorization_url(
        settings.google_callback_url
    )
    return RedirectResponse(redirect_url)


@google_router.get("/callback", status_code=200, tags=["google"])
async def google_auth_callback(
    session: SessionDep,
    profile: google.GoogleProfileDep,
) -> User:
    new_user = await user.get_user_from_google_profile(session, profile)
    return User.model_validate(new_user)


router.include_router(google_router)
