from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.movement import CreateMovementRequest, Movement, UpdateMovementRequest
from app.responses import AuthorizationError, NotFoundError, responses
from app.transfers.crud import (
    create_transfer,
    delete_transfer,
    get_transfer,
    list_transfers,
    update_transfer,
)

router = APIRouter(prefix="/trips/{tripId}/transfers", tags=["transfers"])
TripId = Annotated[int, Path(alias="tripId", gt=0, le=9223372036854775807)]
TransferId = Annotated[int, Path(alias="transferId", gt=0, le=9223372036854775807)]


@router.get("", responses=responses(AuthorizationError, NotFoundError))
async def get_transfers(
    trip_id: TripId, session: SessionDep, user: CurrentUser
) -> list[Movement]:
    return await list_transfers(session, user.primary_key, trip_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, NotFoundError),
)
async def post_transfer(
    trip_id: TripId,
    request: CreateMovementRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Movement:
    return await create_transfer(session, user.primary_key, trip_id, request)


@router.get("/{transferId}", responses=responses(AuthorizationError, NotFoundError))
async def get_transfer_by_id(
    trip_id: TripId, transfer_id: TransferId, session: SessionDep, user: CurrentUser
) -> Movement:
    return await get_transfer(session, user.primary_key, trip_id, transfer_id)


@router.patch("/{transferId}", responses=responses(AuthorizationError, NotFoundError))
async def patch_transfer(
    trip_id: TripId,
    transfer_id: TransferId,
    request: UpdateMovementRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Movement:
    return await update_transfer(
        session, user.primary_key, trip_id, transfer_id, request
    )


@router.delete(
    "/{transferId}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError),
)
async def remove_transfer(
    trip_id: TripId, transfer_id: TransferId, session: SessionDep, user: CurrentUser
) -> Response:
    await delete_transfer(session, user.primary_key, trip_id, transfer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
