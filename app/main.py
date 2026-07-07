from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import JSONResponse

from app.api.impl.user import create_admin_user
from app.api.routers import admin_router, auth_router
from app.database import get_session, setup_tables
from app.models.status import Status
from app.responses import HTTPException, responses


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await setup_tables()

    async for session in get_session():
        await create_admin_user(session)

    yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
app.include_router(admin_router)


@app.exception_handler(HTTPException)
async def handle_http_exceptions(_app, error: HTTPException):
    return JSONResponse(
        error.dump_python(),
        status_code=error.status_code,
    )


@app.get("/health", responses=responses())
async def health() -> Status:
    return Status(status="ok")
