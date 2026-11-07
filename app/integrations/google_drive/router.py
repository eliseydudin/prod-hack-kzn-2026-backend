from typing import Annotated

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from starlette.responses import RedirectResponse

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.integrations.google_drive.errors import (
    PhotoTooLargeError,
    UnsupportedPhotoError,
)
from app.integrations.google_drive.media import read_photo
from app.integrations.google_drive.provider import Provider
from app.integrations.google_drive.service import (
    OAUTH_TTL_SECONDS,
    DriveService,
    connection_status,
    get_export,
)
from app.models.google_drive import (
    GoogleDriveConnect,
    GoogleDriveConnection,
    GoogleDriveExport,
)
from app.models.types.snowflake_id import SnowflakeId
from app.rate_limit import limit_user_requests
from app.responses import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    responses,
)
from app.settings import settings

PREFIX = "/integrations/google-drive"
router = APIRouter(tags=["google-drive"])
COOKIE_NAME = "travelhub_drive_oauth"


async def get_drive_service(session: SessionDep):
    if not settings.google_drive_enabled:
        raise ServiceUnavailableError(detail={"reason": "google_drive_disabled"})
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=5, pool=5),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        follow_redirects=False,
    ) as http:
        yield DriveService(session, Provider(http, settings), settings)


DriveServiceDep = Annotated[DriveService, Depends(get_drive_service)]
AUTH_ERRORS = responses(AuthorizationError, ConflictError, ServiceUnavailableError)
protected_router = APIRouter(
    prefix=PREFIX,
    dependencies=[Depends(limit_user_requests)],
    responses=responses(RateLimitError),
)


@protected_router.post(
    "/connect", response_model=GoogleDriveConnect, responses=AUTH_ERRORS
)
async def connect(user: CurrentUser, service: DriveServiceDep, response: Response):
    result, cookie = await service.begin(user)
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=OAUTH_TTL_SECONDS,
        path="/",
        httponly=True,
        secure=settings.google_drive_redirect_uri.scheme == "https",
        samesite="lax",
    )
    return result


@router.get(
    f"{PREFIX}/callback",
    response_class=RedirectResponse,
    status_code=303,
    responses=responses(BadRequestError, ConflictError, ServiceUnavailableError),
)
async def callback(
    request: Request,
    service: DriveServiceDep,
    state: Annotated[str, Query(min_length=32, max_length=256)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
):
    result = await service.callback(
        state, request.cookies.get(COOKIE_NAME), code, denied=error is not None
    )
    response = RedirectResponse(
        f"{settings.google_drive_return_path}?googleDrive={result}", status_code=303
    )
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.google_drive_redirect_uri.scheme == "https",
        samesite="lax",
    )
    return response


@protected_router.get(
    "", response_model=GoogleDriveConnection, responses=responses(AuthorizationError)
)
async def status(user: CurrentUser, session: SessionDep):
    return await connection_status(session, user, settings.google_drive_enabled)


@protected_router.delete(
    "", response_model=GoogleDriveConnection, responses=AUTH_ERRORS
)
async def disconnect(user: CurrentUser, service: DriveServiceDep):
    return await service.disconnect(user)


@protected_router.post(
    "/photo-exports",
    response_model=GoogleDriveExport,
    status_code=201,
    responses=responses(
        AuthorizationError,
        BadRequestError,
        ConflictError,
        ServiceUnavailableError,
        PhotoTooLargeError,
        UnsupportedPhotoError,
    ),
)
async def export_photo(
    user: CurrentUser,
    service: DriveServiceDep,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    ],
    file: Annotated[UploadFile, File()],
):
    try:
        photo = await read_photo(file, settings.google_drive_max_photo_bytes)
    finally:
        await file.close()
    return await service.export(user, idempotency_key, photo)


@protected_router.get(
    "/photo-exports/{export_id}",
    response_model=GoogleDriveExport,
    responses=responses(AuthorizationError, NotFoundError),
)
async def export_status(
    export_id: Annotated[SnowflakeId, Path(gt=0)],
    user: CurrentUser,
    session: SessionDep,
):
    return await get_export(session, user, export_id)


router.include_router(protected_router)
