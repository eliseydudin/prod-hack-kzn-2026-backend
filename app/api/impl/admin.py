from sqlmodel import col, select

from app.database import SessionDep
from app.models.user import User, UserPaginationResult, UserTable


async def list_users(
    session: SessionDep,
    page: int,
    size: int,
    filter_enabled: bool,
) -> UserPaginationResult:
    query = (
        select(UserTable)
        .limit(size + 1)
        .offset(page * size)
        .order_by(col(UserTable.updated_at).desc())
    )
    if filter_enabled:
        query = query.where(UserTable.enabled)

    result = [
        User.model_validate(ut) for ut in (await session.execute(query)).scalars()
    ]
    is_at_end = len(result) <= size
    if not is_at_end:
        result = result[:-1]

    return UserPaginationResult(
        items=result,
        amount=len(result),
        size=size,
        page=page,
        is_at_end=is_at_end,
    )
