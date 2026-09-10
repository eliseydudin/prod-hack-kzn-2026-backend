from typing import Annotated, Union

import jwt
from fastapi import Cookie, Depends, Response
from fastapi.security.oauth2 import OAuth2PasswordBearer
from pydantic import ValidationError
from sqlmodel import select

from app.database.connection import SessionDep
from app.models.auth import AuthToken, RefreshToken
from app.models.base import Base
from app.models.types.time import utc_now
from app.models.user import UserRole, UserTable
from app.responses.exceptions import AuthorizationError, ForbiddenError, NotFoundError
from app.settings import settings

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
    refreshUrl="/auth/refresh",
)
oauth2_scheme.make_not_authenticated_error = lambda: AuthorizationError(
    message="Not authenticated"
)  # type: ignore


def encode_token(token: Union[RefreshToken, AuthToken]) -> str:
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


async def get_current_user_payload(token: str = Depends(oauth2_scheme)):
    try:
        return decode_token(token, AuthToken)
    except ValidationError, jwt.PyJWTError:
        raise AuthorizationError()


CurrentUserPayload = Annotated[AuthToken, Depends(get_current_user_payload)]


async def get_current_user(session: SessionDep, auth_token: CurrentUserPayload):
    try:
        query = select(UserTable).where(UserTable.id == auth_token.sub)
        user = await session.exec_get_one(query)

        if not user.enabled:
            raise NotFoundError

        return user
    except jwt.PyJWTError, NotFoundError:
        raise AuthorizationError


CurrentUser = Annotated[UserTable, Depends(get_current_user)]


async def get_current_admin_payload(payload: CurrentUserPayload):
    if payload.role not in (UserRole.ADMIN, UserRole.BOOTSTRAP):
        raise ForbiddenError
    return payload


CurrentAdminPayload = Annotated[AuthToken, Depends(get_current_admin_payload)]


async def get_current_admin(session: SessionDep, auth_token: CurrentAdminPayload):
    return await get_current_user(session, auth_token)


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
        samesite="lax",
    )


def delete_refresh_token(response: Response):
    response.delete_cookie(key="refresh_token")


def get_refresh_token(refresh_token: str = Cookie()):
    tok = decode_token(refresh_token, RefreshToken)
    if utc_now() > tok.exp:
        raise AuthorizationError
    return tok


RefreshTokenDep = Annotated[RefreshToken, Depends(get_refresh_token)]
