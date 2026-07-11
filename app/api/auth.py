from typing import Annotated, TypeVar, Union

import jwt
from fastapi import Depends
from fastapi.security.oauth2 import OAuth2PasswordBearer
from pydantic import ValidationError
from sqlmodel import select

from app.database.connection import SessionDep
from app.models.auth import AuthToken, RefreshToken
from app.models.base import Base
from app.models.user import UserRole, UserTable
from app.responses.exceptions import AuthorizationError, ForbiddenError, NotFoundError
from app.settings import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_scheme.make_not_authenticated_error = lambda: AuthorizationError(
    message="Not authenticated"
)  # type: ignore


def encode_token(token: Union[RefreshToken, AuthToken]) -> str:
    return jwt.encode(
        token.model_dump(mode="json"),
        key=settings.jwt_token,
        algorithm="HS256",
    )


_Model = TypeVar("_Model", bound=Base)


def decode_token(token: str, model_type: type[_Model]) -> _Model:
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


async def get_current_admin(user: CurrentUser):
    if user.role != UserRole.ADMIN:
        raise ForbiddenError
    return user


CurrentAdmin = Annotated[UserTable, Depends(get_current_admin)]
