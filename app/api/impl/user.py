from logging import getLogger

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.google.models import GoogleProfile
from app.database.session import Session
from app.models.types.missing import is_missing
from app.models.types.snowflake_id import SnowflakeId
from app.models.user import (
    CreateUserRequest,
    EditUserRequest,
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

logger = getLogger(__name__)


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


async def create_bootstrap_user(session: Session):
    if not settings.bootstrap_username or not settings.bootstrap_password:
        return

    username = settings.bootstrap_username
    password = settings.bootstrap_password
    query = select(UserTable).where(UserTable.role == UserRole.BOOTSTRAP)

    try:
        if _admin := await session.exec_get_one_or_none(query):
            raise ConflictError("attempted to create a new bootstrap user")

        request = CreateUserRequest(
            username=username,
            password=password,
            role=UserRole.BOOTSTRAP,
        )

        await create_user(session, request)
    except ConflictError as e:
        logger.warning("consider removing bootstrap credentials from env", exc_info=e)


DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$gD2/ebpnsnqEE8pEQAyI3g$TXaLDLSY+4mh/joQdYC8kV1D5lXewdL6BOyAX0QnxWY"


async def login_user(session: Session, username: str, password: str):
    try:
        query = select(UserTable).where(UserTable.username == username)
        user = await session.exec_get_one_or_none(query)

        if user is None:
            check_password(DUMMY_HASH, "dummy_password")
            raise NotFoundError

        if user.enabled and user.password and check_password(user.password, password):
            return user

        raise NotFoundError

    except NotFoundError:
        raise AuthorizationError


async def edit_user(session: Session, user: UserTable, request: EditUserRequest):
    updated_fields = set[str]()

    if not is_missing(request.username):
        user.username = request.username
        updated_fields.add("username")

    try:
        session.add(user)
        await session.commit()
        await session.refresh(user)

        return user
    except IntegrityError:
        raise ConflictError(
            message="Conflict in updating the user's fields",
            detail={"conflicted": list(updated_fields)},
        )


async def delete_user(session: Session, user: UserTable):
    if user.role == UserRole.ADMIN:
        raise ConflictError(
            message="Can't delete this user, because they are an admin",
            detail={"username": user.username, "sub": user.id},
        )

    user.enabled = False
    session.add(user)
    await session.commit()


async def create_user_from_google_profile(session: Session, profile: GoogleProfile):
    new_username = profile.email.split("@")[0]
    new_username = new_username[:32]

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


async def get_user_from_google_profile(
    session: Session,
    profile: GoogleProfile,
) -> tuple[bool, UserTable]:
    """
    If an account associated with this profile already exists, return False and itself.
    Otherwise return True and the newly created account.
    """
    query = (
        select(UserTable)
        .join(UserIdentityTable)
        .where(
            UserTable.identity is not None,
            UserIdentityTable.source == UserIdentitySource.GOOGLE,
            UserIdentityTable.identifier == profile.email,
        )
    )

    if user := await session.exec_get_one_or_none(query):
        return False, user

    return True, await create_user_from_google_profile(session, profile)


async def get_user_by_id(session: Session, id: SnowflakeId):
    query = select(UserTable).where(UserTable.id == id)
    return await session.exec_get_one(query)
