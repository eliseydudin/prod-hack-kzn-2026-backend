from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.database import Session
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import (
    CreateUserRequest,
    UserRole,
    UserTable,
)
from app.responses import ConflictError, ErrorCode


async def add_or_update_user(session: Session, user: UserTable):
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_id(
    session: Session,
    id: SnowflakeId,
    check_enabled: bool = True,
):
    query = select(UserTable).where(UserTable.id == id)
    user = await session.scalar(query)

    if user:
        if not user.enabled and check_enabled:
            return None
        return user


async def get_bootstrap_user(session: Session):
    query = select(UserTable).where(UserTable.role == UserRole.BOOTSTRAP)
    return await session.scalar(query)


async def get_user_by_username(
    session: Session,
    username: str,
    check_enabled: bool = True,
):
    query = select(UserTable).where(UserTable.username == username)
    user = await session.scalar(query)

    if user:
        if not user.enabled and check_enabled:
            return None
        return user


async def create_user(
    session: Session,
    request: CreateUserRequest,
    hasher: Callable[[str], str],
):
    user = UserTable.model_validate(
        request,
        update={
            "password": hasher(request.password),
        },
    )
    try:
        return await add_or_update_user(session, user)
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(error_code=ErrorCode.USERNAME_IS_ALREADY_IN_USE) from error


async def delete_user(session: Session, user: UserTable):
    user.enabled = False
    await add_or_update_user(session, user)


async def list_users(
    session: Session,
    page: int,
    size: int,
):
    query = (
        select(UserTable)
        .limit(size + 1)
        .offset(page * size)
        .order_by(col(UserTable.updated_at).desc())
    )

    result = (await session.execute(query)).scalars().all()
    is_at_end = len(result) <= size
    if not is_at_end:
        result = result[:-1]

    return is_at_end, result
