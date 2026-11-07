from typing import Annotated

from fastapi import APIRouter, Path, status

from app.auth.tokens import CurrentUser
from app.database import SessionDep
from app.events import crud
from app.models.event import CreateEventRequest, Event, UpdateEventRequest
from app.models.types.snowflake_id import SnowflakeId
from app.responses import AuthorizationError, NotFoundError, ValidationError, responses

router = APIRouter(prefix="/trips/{tripId}/events", tags=["events"])
TripId = Annotated[SnowflakeId, Path(alias="tripId", gt=0)]
EventId = Annotated[SnowflakeId, Path(alias="eventId", gt=0)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, NotFoundError, ValidationError),
)
async def post_event(
    trip_id: TripId, request: CreateEventRequest, session: SessionDep, user: CurrentUser
) -> Event:
    return await crud.create_event(session, user.primary_key, trip_id, request)


@router.get("", responses=responses(AuthorizationError, NotFoundError))
async def get_events(
    trip_id: TripId, session: SessionDep, user: CurrentUser
) -> list[Event]:
    return await crud.list_events(session, user.primary_key, trip_id)


@router.get("/{eventId}", responses=responses(AuthorizationError, NotFoundError))
async def get_event(
    trip_id: TripId, event_id: EventId, session: SessionDep, user: CurrentUser
) -> Event:
    return await crud.get_event(session, user.primary_key, trip_id, event_id)


@router.patch(
    "/{eventId}",
    responses=responses(AuthorizationError, NotFoundError, ValidationError),
)
async def patch_event(
    trip_id: TripId,
    event_id: EventId,
    request: UpdateEventRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Event:
    return await crud.update_event(
        session, user.primary_key, trip_id, event_id, request
    )


@router.delete(
    "/{eventId}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError),
)
async def remove_event(
    trip_id: TripId, event_id: EventId, session: SessionDep, user: CurrentUser
) -> None:
    await crud.delete_event(session, user.primary_key, trip_id, event_id)
