from logging import getLogger

from fastapi import APIRouter, Query

from app.api.auth import CurrentAdmin
from app.api.impl import user
from app.api.impl.admin import list_users
from app.database.connection import SessionDep
from app.models.user import CreateUserRequest, User, UserPaginationResult
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
    create_req: CreateUserRequest,
) -> User:
    new_user = User.model_validate(await user.create_user(session, create_req))
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
    filter_enabled: bool = Query(False, alias="filterEnabled"),
) -> UserPaginationResult:
    return await list_users(session, page, size, filter_enabled)
