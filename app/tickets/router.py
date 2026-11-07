from asyncio import gather
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, Response, UploadFile, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.ticket import (
    TicketApplyRequest,
    TicketApplyResult,
    TicketKind,
    TicketPlaceMatch,
    TicketPreview,
)
from app.models.types.snowflake_id import SnowflakeId
from app.responses import AuthorizationError, NotFoundError, ValidationError, responses
from app.tickets.crud import apply_ticket
from app.tickets.geocoding import resolve_ticket_place
from app.tickets.parser import TicketParseError, parse_ticket
from app.tickets.source import TicketSourceError, read_ticket_text
from app.trips.crud import get_owned_trip

router = APIRouter(prefix="/trips/{tripId}/tickets", tags=["tickets"])


@router.post("/preview", responses=responses(AuthorizationError, NotFoundError))
async def post_ticket_preview(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[UploadFile | None, File()] = None,
    url: Annotated[str | None, Form()] = None,
    kind: Annotated[TicketKind | None, Form()] = None,
) -> TicketPreview:
    await get_owned_trip(session, user, trip_id)
    await session.rollback()
    try:
        parsed = parse_ticket(await read_ticket_text(file, url), kind)
    except (TicketSourceError, TicketParseError) as error:
        raise ValidationError(errors=[str(error)]) from error

    points = await gather(
        *(resolve_ticket_place(place, parsed.kind) for place in parsed.places)
    )
    places = [
        TicketPlaceMatch.model_validate(place, update={"point": point})
        for place, point in zip(parsed.places, points, strict=True)
    ]
    preview = TicketPreview(
        kind=parsed.kind, started_at=parsed.started_at, ended_at=parsed.ended_at
    )
    if parsed.kind == TicketKind.STAY:
        preview.stay = places[0]
    else:
        preview.departure, preview.arrival = places
    response.headers["Cache-Control"] = "no-store"
    return preview


@router.post(
    "/apply",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, NotFoundError),
)
async def post_ticket_apply(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    request: TicketApplyRequest,
    session: SessionDep,
    user: CurrentUser,
) -> TicketApplyResult:
    return await apply_ticket(session, user, trip_id, request)
