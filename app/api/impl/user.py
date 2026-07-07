from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.database.session import Session
from app.models.user import CreateUserRequest, UserRole, UserTable
from app.responses.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.settings import settings
from app.utils.hasher import check_password, hash_password


async def create_user(session: Session, req: CreateUserRequest):
    user_table = UserTable.model_validate(
        req, update={"password": hash_password(req.password)}
    )

    try:
        session.add(user_table)
        await session.commit()
        await session.refresh(user_table)
        return user_table
    except IntegrityError:
        raise ConflictError(message="A user with this name already exists")


async def create_admin_user(session: Session):
    if not settings.admin_credentials:
        return

    username, password = settings.unwrap_admin_credentials()
    query = select(UserTable).where(UserTable.role == UserRole.ADMIN)

    if admin := await session.exec_get_one_or_none(query):
        if admin.username == username:
            return

        admin.role = UserRole.USER
        session.add(admin)

    request = CreateUserRequest(
        username=username,
        password=password,
        role=UserRole.ADMIN,
    )

    await create_user(session, request)


async def login_user(session: Session, username: str, password: str):
    try:
        query = select(UserTable).where(UserTable.username == username)
        user = await session.exec_get_one(query)

        if user.enabled and user.password and check_password(user.password, password):
            return user

        raise NotFoundError

    except NotFoundError:
        raise AuthorizationError


async def delete_user(session: Session, user: UserTable, hard: bool = False):
    if user.role == UserRole.ADMIN:
        raise ConflictError(
            message="Can't delete this user, because they are an admin",
            detail={"username": user.username, "sub": user.id},
        )

    if not hard:
        user.enabled = False
        session.add(user)
    else:
        await session.delete(user)

    await session.commit()
