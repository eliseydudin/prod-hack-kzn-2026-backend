from typing import TypeVar, Union

import jwt
from sqlmodel import select

from app.database.session import Session
from app.models.auth import AuthToken, RefreshToken
from app.models.base import Base
from app.models.user import UserTable
from app.responses.exceptions import AuthorizationError, NotFoundError
from app.settings import settings


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


async def get_user_by_token(session: Session, token: str):
    try:
        auth_token = decode_token(token, AuthToken)
        query = select(UserTable).where(UserTable.id == auth_token.sub)
        user = await session.exec_get_one(query)

        if not user.enabled:
            raise NotFoundError

        return user
    except jwt.PyJWTError, NotFoundError:
        raise AuthorizationError
