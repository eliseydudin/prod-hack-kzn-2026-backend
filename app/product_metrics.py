from asyncio import TaskGroup, sleep, timeout
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from json import dumps
from logging import getLogger
from time import time

from fastapi import BackgroundTasks
from sqlalchemy import distinct, true
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, func, select
from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from app.auth.tokens import CurrentUser
from app.database import get_session_ctx
from app.models.collaborator import CollaboratorTable
from app.models.event import EventTable
from app.models.trip import TripTable
from app.models.types.time import utc_now
from app.models.user import UserRole, UserTable
from app.models.waypoint import WaypointTable
from app.rate_limit import VALKEY_TIMEOUT, RateLimiter, RateLimiterDep

logger = getLogger(__name__)
ACTIVITY_KEY = "travelhub:product:v1:last_trip_activity"
DAY = timedelta(days=1)
WEEK = timedelta(days=7)


async def _record_activity(client: Valkey, user_id: int, seen_at: float) -> None:
    try:
        async with timeout(VALKEY_TIMEOUT):
            await client.zadd(ACTIVITY_KEY, {str(user_id): seen_at}, gt=True)
    except TimeoutError, ValkeyError:
        pass


async def track_trip_activity(
    tasks: BackgroundTasks, limiter: RateLimiterDep, user: CurrentUser
) -> None:
    if limiter is not None and user.role == UserRole.USER:
        tasks.add_task(_record_activity, limiter.client, user.id, time())


async def _active_users(client: Valkey, now: datetime) -> dict[str, int]:
    day_ago = (now - DAY).timestamp()
    week_ago = (now - WEEK).timestamp()
    async with client.pipeline(transaction=True) as pipeline:
        pipeline.zremrangebyscore(ACTIVITY_KEY, "-inf", week_ago)
        pipeline.zcount(ACTIVITY_KEY, f"({day_ago}", now.timestamp())
        pipeline.zcount(ACTIVITY_KEY, f"({week_ago}", now.timestamp())
        _, daily, weekly = await pipeline.execute()
    return {"active_users_24h": daily, "active_users_7d": weekly}


async def _account_metrics(now: datetime) -> dict[str, int]:
    enabled = col(UserTable.enabled).is_(True)
    recent_account = col(UserTable.created_at) > now - WEEK
    trip_id = col(TripTable.primary_key)
    owner_id = col(TripTable.owner_id)
    owners = func.count(distinct(owner_id))
    trips = func.count()
    has_plan = trip_id.in_(select(col(WaypointTable.trip_id))) | trip_id.in_(
        select(col(EventTable.trip_id))
    )
    has_mapped_event = trip_id.in_(
        select(col(EventTable.trip_id)).where(
            col(EventTable.waypoint_primary_key).is_not(None)
        )
    )
    is_shared = trip_id.in_(select(col(CollaboratorTable.trip_id)))
    has_multiple_trips = owner_id.in_(
        select(owner_id).group_by(owner_id).having(func.count() > 1)
    )
    accounts = (
        select(
            func.count().filter(enabled).label("enabled_accounts"),
            func.count()
            .filter(col(UserTable.created_at) > now - DAY)
            .label("new_accounts_24h"),
            func.count()
            .filter(enabled, recent_account)
            .label("new_enabled_accounts_7d"),
        )
        .select_from(UserTable)
        .where(col(UserTable.role) == UserRole.USER)
        .subquery()
    )
    planning = (
        select(
            owners.label("trip_owners"),
            owners.filter(has_mapped_event).label("trip_owners_with_mapped_events"),
            owners.filter(recent_account).label("new_accounts_with_trip_7d"),
            owners.filter(recent_account, has_plan).label("new_accounts_with_plan_7d"),
            owners.filter(has_multiple_trips).label("repeat_trip_owners"),
            trips.label("trips"),
            trips.filter(col(TripTable.created_at) > now - WEEK).label(
                "trips_created_7d"
            ),
            trips.filter(has_plan).label("trips_with_plan"),
            trips.filter(is_shared).label("shared_trips"),
        )
        .select_from(TripTable)
        .join(UserTable, col(UserTable.primary_key) == owner_id)
        .where(col(UserTable.role) == UserRole.USER, enabled)
        .subquery()
    )
    async with get_session_ctx() as session:
        result = await session.execute(
            select(*accounts.c, *planning.c).select_from(
                accounts.join(planning, true())
            )
        )
    return dict(result.mappings().one())


async def _publish_snapshots(client: Valkey) -> None:
    while True:
        now = utc_now()
        try:
            async with timeout(5):
                metrics = await _active_users(client, now) | await _account_metrics(now)
            metrics["sample_timestamp_seconds"] = int(now.timestamp())
            print(
                dumps({"event": "travelhub_product_snapshot", "metrics": metrics}),
                flush=True,
            )
        except TimeoutError, OSError, SQLAlchemyError, ValkeyError:
            logger.warning("Could not collect product metrics")
        await sleep(60)


@asynccontextmanager
async def product_metrics_lifespan(limiter: RateLimiter | None):
    if limiter is None:
        yield
        return

    async with TaskGroup() as tasks:
        sampler = tasks.create_task(_publish_snapshots(limiter.client))
        try:
            yield
        finally:
            sampler.cancel()
