from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Response
from fastapi.security.oauth2 import OAuth2PasswordBearer
from pydantic import ValidationError

from app.database.connection import SessionDep
from app.database.session import Session
from app.models.auth import AuthToken, RefreshToken
from app.models.base import Base
from app.models.types.time import utc_now
from app.models.user import UserRole, UserTable
from app.responses import AuthorizationError, ForbiddenError, NotFoundError
from app.settings import settings
from app.users.crud import get_user_by_id

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
    refreshUrl="/auth/refresh",
    auto_error=False,
)
oauth2_scheme.make_not_authenticated_error = lambda: AuthorizationError()  # type: ignore


def encode_token(token: RefreshToken | AuthToken) -> str:
    return jwt.encode(
        token.model_dump(mode="json"),
        key=settings.jwt_token,
        algorithm="HS256",
    )


def decode_token[_Model: Base](token: str, model_type: type[_Model]) -> _Model:
    return model_type.model_validate(
        jwt.decode(
            token,
            key=settings.jwt_token,
            algorithms=["HS256"],
        )
    )


async def _get_current_user(session: Session, token: str):
    try:
        auth_token = decode_token(token, AuthToken)
        user = await get_user_by_id(session, auth_token.sub)

        if not user:
            raise NotFoundError

        return user
    except ValidationError, jwt.PyJWTError, NotFoundError:
        raise AuthorizationError


async def get_current_user(
    session: SessionDep,
    token: str | None = Depends(oauth2_scheme),
):
    if not token:
        raise AuthorizationError
    return await _get_current_user(session, token)


async def get_current_optional_user(
    session: SessionDep,
    token: str | None = Depends(oauth2_scheme),
):
    return await _get_current_user(session, token) if token else None


CurrentUser = Annotated[UserTable, Depends(get_current_user)]
CurrentOptionalUser = Annotated[UserTable | None, Depends(get_current_optional_user)]


async def get_current_admin(user: CurrentUser):
    if user.role not in (UserRole.ADMIN, UserRole.BOOTSTRAP):
        raise ForbiddenError
    return user


CurrentAdmin = Annotated[UserTable, Depends(get_current_admin)]


def send_refresh_token(user: UserTable, response: Response):
    token = RefreshToken(sub=user.id)
    encoded = encode_token(token)
    response.set_cookie(
        key="refresh_token",
        value=encoded,
        expires=token.exp,
        domain=settings.cookie_domain,
        httponly=True,
        samesite="none",
        secure=True,
        partitioned=True,
    )


def delete_refresh_token(response: Response):
    response.delete_cookie(key="refresh_token")


def get_refresh_token(refresh_token: str = Cookie()):
    tok = decode_token(refresh_token, RefreshToken)
    if utc_now() > tok.exp:
        raise AuthorizationError
    return tok


RefreshTokenDep = Annotated[RefreshToken, Depends(get_refresh_token)]
