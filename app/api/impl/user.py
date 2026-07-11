import secrets

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.google.models import GoogleProfile
from app.database.session import Session
from app.models.user import (
    CreateUserRequest,
    UserIdentitySource,
    UserIdentityTable,
    UserRole,
    UserTable,
)
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
    query = select(UserTable).where(UserTable.username == username)

    if admin := await session.exec_get_one_or_none(query):
        admin.role = UserRole.ADMIN
        admin.password = hash_password(password)
        session.add(admin)
        await session.commit()
        return

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


async def create_user_from_google_profile(session: Session, profile: GoogleProfile):
    new_username = profile.email.split("@")[0] + "." + secrets.token_hex(4)

    identity = UserIdentityTable(
        source=UserIdentitySource.GOOGLE,
        identifier=profile.email,
    )
    user = UserTable(
        username=new_username,
        password=None,
        identity=identity,
    )

    session.add(identity)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_from_google_profile(session: Session, profile: GoogleProfile):
    """
    If an account created via this profile already exists, return it. Otherwise create a new account.
    """
    query = (
        select(UserTable)
        .join(UserIdentityTable)
        .where(
            UserTable.identity is not None,
            UserIdentityTable.source == UserIdentitySource.GOOGLE,  # type: ignore
            UserIdentityTable.identifier == profile.email,  # type: ignore
        )
    )

    if user := await session.exec_get_one_or_none(query):
        return user

    return await create_user_from_google_profile(session, profile)
