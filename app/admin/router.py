from logging import getLogger

from fastapi import APIRouter, Query

from app import users
from app.auth.hasher import hash_password
from app.auth.tokens import CurrentAdmin
from app.database.connection import SessionDep
from app.models.user import (
    AdminCreateUserRequest,
    User,
    UserPaginationResult,
)
from app.responses import AuthorizationError, ConflictError, ForbiddenError, responses

logger = getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/user/create",
    responses=responses(
        AuthorizationError,
        ForbiddenError,
        ConflictError,
    ),
    status_code=201,
)
async def create_user(
    admin: CurrentAdmin,
    session: SessionDep,
    create_req: AdminCreateUserRequest,
) -> User:
    new_user = User.model_validate(
        await users.create_user(
            session,
            create_req,
            hash_password,
        )
    )
    logger.info(
        "Created a new user @%s (%s) by admin @%s (%s)",
        new_user.username,
        new_user.id,
        admin.username,
        admin.id,
    )
    return new_user


@router.get(
    "/user/all",
    responses=responses(AuthorizationError, ForbiddenError),
    status_code=200,
)
async def list_all_users(
    _admin: CurrentAdmin,
    session: SessionDep,
    page: int = Query(0, ge=0),
    size: int = Query(20, gt=0),
) -> UserPaginationResult:
    is_at_end, result = await users.list_users(session, page, size)
    result = [User.model_validate(user) for user in result]
    return UserPaginationResult(
        items=result,
        size=size,
        page=page,
        amount=len(result),
        is_at_end=is_at_end,
    )
