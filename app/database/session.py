from typing import Any, Optional, TypeVar

from sqlalchemy import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import TypedReturnsRows

_T = TypeVar("_T", bound=Any)


class Session(AsyncSession):
    async def exec(self, statement: TypedReturnsRows[_T]) -> ScalarResult[_T]:
        result = await self.execute(statement)
        return result.scalars()

    async def exec_get_one_or_none(
        self,
        statement: TypedReturnsRows[tuple[_T]],
    ) -> Optional[_T]:
        return (await self.exec(statement)).one_or_none()  # type: ignore

    async def exec_get_one(self, statement: TypedReturnsRows[tuple[_T]]) -> _T:
        return (await self.exec(statement)).one()  # type: ignore
