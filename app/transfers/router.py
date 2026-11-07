from typing import Annotated

from fastapi import APIRouter, Path, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.movement import CreateMovementRequest, Movement, UpdateMovementRequest
from app.models.types.snowflake_id import SnowflakeId
from app.responses import AuthorizationError, NotFoundError, responses
from app.transfers.crud import (
    create_transfer,
    delete_transfer,
    get_transfer,
    list_transfers,
    update_transfer,
)

router = APIRouter(prefix="/trips/{tripId}/transfers", tags=["transfers"])


@router.get("", responses=responses(AuthorizationError, NotFoundError))
async def get_transfers(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> list[Movement]:
    return await list_transfers(session, user, trip_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=responses(AuthorizationError, NotFoundError),
)
async def post_transfer(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    request: CreateMovementRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Movement:
    return await create_transfer(session, user, trip_id, request)


@router.get("/{transferId}", responses=responses(AuthorizationError, NotFoundError))
async def get_transfer_by_id(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    transfer_id: Annotated[SnowflakeId, Path(alias="transferId", gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> Movement:
    return await get_transfer(session, user, trip_id, transfer_id)


@router.patch("/{transferId}", responses=responses(AuthorizationError, NotFoundError))
async def patch_transfer(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    transfer_id: Annotated[SnowflakeId, Path(alias="transferId", gt=0)],
    request: UpdateMovementRequest,
    session: SessionDep,
    user: CurrentUser,
) -> Movement:
    return await update_transfer(session, user, trip_id, transfer_id, request)


@router.delete(
    "/{transferId}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(AuthorizationError, NotFoundError),
)
async def remove_transfer(
    trip_id: Annotated[SnowflakeId, Path(alias="tripId", gt=0)],
    transfer_id: Annotated[SnowflakeId, Path(alias="transferId", gt=0)],
    session: SessionDep,
    user: CurrentUser,
) -> None:
    await delete_transfer(session, user, trip_id, transfer_id)
