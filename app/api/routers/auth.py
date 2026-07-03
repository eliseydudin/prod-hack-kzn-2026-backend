from typing import Annotated

from fastapi import Depends
from fastapi.routing import APIRouter
from fastapi.security.oauth2 import OAuth2PasswordRequestFormStrict

from app.api.auth import CurrentUser, encode_token
from app.api.impl import user
from app.database import SessionDep
from app.models.auth import AuthToken, AuthTokenResponse
from app.models.user import CreateUserRequest, RegisterUserRequest, User
from app.responses import ConflictError, responses
from app.responses.exceptions import AuthorizationError

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
