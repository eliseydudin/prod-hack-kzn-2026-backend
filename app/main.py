from contextlib import asynccontextmanager
from logging import getLogger

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.admin.router import router as admin_router
from app.auth.impl import create_bootstrap_user
from app.auth.router import change_password
from app.auth.router import router as auth_router
from app.database import get_session_ctx, setup_tables
from app.events.router import router as events_router
from app.models.status import Status
from app.points.router import router as points_router
from app.product_metrics import product_metrics_lifespan, track_trip_activity
from app.push.router import router as push_router
from app.rate_limit import limit_user_requests, rate_limit_lifespan
from app.responses import HTTPException, RateLimitError, ValidationError, responses
from app.settings import settings
from app.storage import setup_storage
from app.transfers.router import router as transfers_router
from app.trips.router import router as trips_router
from app.utils.logger import setup_logger

logger = getLogger("app")


def generate_unique_name(route: APIRoute):
    tag = route.tags[0] if route.tags else "root"
    return f"{tag}-{route.name}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger(logger)
    await setup_tables()
    async with get_session_ctx() as sesh:
        await create_bootstrap_user(sesh)

    app.state.storage = None
    if settings.storage is not None:
        app.state.storage = await run_in_threadpool(setup_storage, settings.storage)

    async with rate_limit_lifespan() as limiter, product_metrics_lifespan(limiter):
        logger.info("Setup complete")
        yield


app = FastAPI(
    lifespan=lifespan,
    generate_unique_id_function=generate_unique_name,
    root_path="/api/v1",
)
app.include_router(auth_router)
app.include_router(admin_router)
for router in (trips_router, points_router, transfers_router, events_router):
    app.include_router(
        router,
        dependencies=[Depends(limit_user_requests), Depends(track_trip_activity)],
        responses=responses(RateLimitError),
    )
app.include_router(push_router)

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
]
if domain := settings.domain:
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
    expose_headers=["Retry-After"],
)


@app.exception_handler(HTTPException)
async def handle_http_exceptions(_app, error: HTTPException):
    headers = (
        {"Retry-After": str(error.retry_after), "Cache-Control": "no-store"}
        if isinstance(error, RateLimitError)
        else None
    )
    return JSONResponse(
        error.dump_python(),
        status_code=error.status_code,
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError):
    validation_error = ValidationError.from_fastapi(error)
    if request.scope.get("endpoint") is change_password:
        validation_error.errors = [
            {key: value for key, value in item.items() if key != "input"}
            for item in validation_error.errors
        ]
    return JSONResponse(
        validation_error.dump_python(),
        status_code=ValidationError.status_code,
    )


@app.get("/health", responses=responses())
async def health() -> Status:
    return Status(status="ok")
