from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.trip import Trip
from app.models.trip_archive import TripArchive
from app.models.types.snowflake_id import SnowflakeId
from app.responses import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
    responses,
)
from app.trips.archive import export_trip, import_trip

MAX_ARCHIVE_BYTES = 1024 * 1024


class ArchiveRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_handler(request: Request) -> Response:
            if self.body_field is None:
                return await handler(request)

            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_ARCHIVE_BYTES:
                    raise PayloadTooLargeError(detail={"reason": "archive_too_large"})
                body.extend(chunk)
            payload = bytes(body)
            delivered = False

            async def receive():
                nonlocal delivered
                if delivered:
                    return await request.receive()
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}

            bounded_request = Request(request.scope, receive)
            try:
                return await handler(bounded_request)
            except RequestValidationError as error:
                errors = [
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"input", "ctx"}
                    }
                    for item in error.errors()
                ]
                raise ValidationError(errors=errors) from error

        return bounded_handler


router = APIRouter(route_class=ArchiveRoute)


@router.post(
    "/import",
    response_model=Trip,
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, PayloadTooLargeError),
)
async def post_trip_import(
    request: TripArchive,
    session: SessionDep,
    user: CurrentUser,
) -> Trip:
    return await import_trip(session, user, request)


@router.get(
    "/{trip_id}/export",
    response_model=TripArchive,
    responses=responses(
        AuthorizationError, NotFoundError, ConflictError, PayloadTooLargeError
    ),
)
async def get_trip_export(
    trip_id: Annotated[SnowflakeId, Path(gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    archive = await export_trip(session, user, trip_id)
    payload = archive.model_dump_json(by_alias=True).encode("utf-8")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise PayloadTooLargeError(detail={"reason": "archive_too_large"})
    return Response(
        payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="trip-{trip_id}.json"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
