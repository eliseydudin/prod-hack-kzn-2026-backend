from contextlib import asynccontextmanager
from logging import getLogger

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from app.api.impl.user import create_bootstrap_user
from app.api.routers import admin_router, auth_router
from app.database import get_session, setup_tables
from app.models.status import Status
from app.responses import HTTPException, responses
from app.settings import settings
from app.utils.iter import async_foreach
from app.utils.logger import setup_logger

logger = getLogger("app")


def generate_unique_name(route: APIRoute):
    tag = route.tags[0] if route.tags else "root"
    return f"{tag}-{route.name}"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logger(logger)
    await setup_tables()
    await async_foreach(get_session(), create_bootstrap_user)

    logger.info("Setup complete")

    yield


app = FastAPI(
    lifespan=lifespan,
    generate_unique_id_function=generate_unique_name,
)
app.include_router(auth_router)
app.include_router(admin_router)

ALLOWED_ORIGINS = ["http://localhost:5173", "http://localhost:8000"]
if domain := settings.base_domain:
    ALLOWED_ORIGINS += [
        f"http://{domain}",
        f"https://{domain}",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_origins=ALLOWED_ORIGINS,
    allow_headers=["*"],
    allow_credentials=True,
)

if frontend_build_path := settings.frontend_build_path:
    app.frontend("/", directory=frontend_build_path)


@app.exception_handler(HTTPException)
async def handle_http_exceptions(_app, error: HTTPException):
    return JSONResponse(
        error.dump_python(),
        status_code=error.status_code,
    )


@app.get("/health", responses=responses())
async def health() -> Status:
    return Status(status="ok")
