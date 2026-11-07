from asyncio import TaskGroup, sleep, timeout
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from json import dumps
from logging import getLogger
from time import time

from fastapi import BackgroundTasks
from sqlalchemy import distinct
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, func, select
from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from app.auth.tokens import CurrentUser
from app.database import get_session_ctx
from app.models.event import EventTable
from app.models.trip import TripTable
from app.models.types.time import utc_now
from app.models.user import UserRole, UserTable
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
    async with get_session_ctx() as session:
        enabled, new = (
            await session.execute(
                select(
                    func.count().filter(col(UserTable.enabled).is_(True)),
                    func.count().filter(col(UserTable.created_at) > now - DAY),
                )
                .select_from(UserTable)
                .where(col(UserTable.role) == UserRole.USER)
            )
        ).one()
        owners = func.count(distinct(col(TripTable.owner_id)))
        with_trips, with_mapped_events = (
            await session.execute(
                select(
                    owners,
                    owners.filter(col(EventTable.waypoint_primary_key).is_not(None)),
                )
                .select_from(TripTable)
                .join(UserTable, col(UserTable.primary_key) == col(TripTable.owner_id))
                .outerjoin(
                    EventTable, col(EventTable.trip_id) == col(TripTable.primary_key)
                )
                .where(
                    col(UserTable.role) == UserRole.USER,
                    col(UserTable.enabled).is_(True),
                )
            )
        ).one()
    return {
        "enabled_accounts": enabled,
        "new_accounts_24h": new,
        "trip_owners": with_trips,
        "trip_owners_with_mapped_events": with_mapped_events,
    }


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
