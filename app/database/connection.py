from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

import app.models  # noqa
from app.database.session import Session

# noqa because sql alchemy needs the table metadata
# to setup the tables in the database
from app.settings import settings

engine = create_async_engine(settings.database_url.unicode_string())
maker = async_sessionmaker(engine, expire_on_commit=False, class_=Session)


async def get_session():
    async with maker() as session:
        yield session


async def setup_tables():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


SessionDep = Annotated[Session, Depends(get_session)]
