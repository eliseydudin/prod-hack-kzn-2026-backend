from sqlmodel import select

from app.database import SessionDep
from app.models.response import PaginationResult
from app.models.user import User, UserTable


async def list_users(
    session: SessionDep,
    page: int,
    size: int,
    filter_enabled: bool,
) -> PaginationResult[User]:
    query = select(UserTable).limit(size).offset(page * size)
    if filter_enabled:
        query = query.where(UserTable.enabled)
    result = [User.model_validate(ut) for ut in (await session.exec(query)).all()]

    return PaginationResult(items=result, amount=len(result), size=size, page=page)
