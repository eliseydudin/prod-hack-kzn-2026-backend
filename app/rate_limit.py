from asyncio import timeout
from contextlib import asynccontextmanager
from functools import cache
from logging import getLogger
from typing import Annotated

from fastapi import Depends, Request
from valkey.asyncio import BlockingConnectionPool, Valkey
from valkey.asyncio.retry import Retry
from valkey.backoff import NoBackoff
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import OutOfMemoryError, ReadOnlyError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from app.auth.tokens import CurrentUser
from app.responses import RateLimitError, ServiceUnavailableError
from app.settings import settings

logger = getLogger(__name__)

VALKEY_TIMEOUT = 0.25
VALKEY_MAX_CONNECTIONS = 32
FIXED_WINDOW = """
local ttl = redis.call("PTTL", KEYS[1])
if ttl == -2 then
    redis.call("SET", KEYS[1], 1, "PX", ARGV[1])
    return {1, tonumber(ARGV[1])}
end
if ttl == -1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {redis.call("INCR", KEYS[1]), ttl}
"""


def create_rate_limit_client(url: str) -> Valkey:
    pool = BlockingConnectionPool.from_url(
        url,
        max_connections=VALKEY_MAX_CONNECTIONS,
        timeout=VALKEY_TIMEOUT,
        socket_connect_timeout=VALKEY_TIMEOUT,
        socket_timeout=VALKEY_TIMEOUT,
        retry=Retry(NoBackoff(), 0),
    )
    return Valkey.from_pool(pool)


class RateLimiter:
    def __init__(self, client: Valkey, window_seconds: int) -> None:
        self.client = client
        self.window_ms = window_seconds * 1000
        self._unavailable = False

    async def enforce(
        self, policy: str, subject: str, limit: int, *, fail_closed: bool = False
    ) -> None:
        key = f"travelhub:rate_limit:v1:{policy}:{subject}"
        try:
            async with timeout(VALKEY_TIMEOUT):
                count, ttl_ms = await self.client.eval(
                    FIXED_WINDOW, 1, key, self.window_ms
                )
        except (
            TimeoutError,
            ValkeyConnectionError,
            ValkeyTimeoutError,
            OutOfMemoryError,
            ReadOnlyError,
        ) as error:
            if not self._unavailable:
                logger.warning("Valkey unavailable for rate limiting")
                self._unavailable = True
            if fail_closed:
                raise ServiceUnavailableError() from error
            return

        if self._unavailable:
            logger.info("Valkey rate limiting recovered")
            self._unavailable = False
        if count > limit:
            raise RateLimitError(retry_after=max(1, (ttl_ms + 999) // 1000))


@cache
def _get_rate_limiter() -> RateLimiter | None:
    if settings.redis_url is None:
        return None
    client = create_rate_limit_client(str(settings.redis_url))
    return RateLimiter(client, settings.rate_limit_window_seconds)


async def get_rate_limiter() -> RateLimiter | None:
    return _get_rate_limiter()


RateLimiterDep = Annotated[RateLimiter | None, Depends(get_rate_limiter)]


@asynccontextmanager
async def rate_limit_lifespan():
    limiter = _get_rate_limiter()
    try:
        yield
    finally:
        _get_rate_limiter.cache_clear()
        if limiter is not None:
            await limiter.client.aclose()


async def _limit_request(
    limiter: RateLimiter | None,
    policy: str,
    subject: str,
    limit: int,
    *,
    fail_closed: bool = False,
) -> None:
    if limiter is not None:
        await limiter.enforce(policy, subject, limit, fail_closed=fail_closed)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def limit_login(request: Request, limiter: RateLimiterDep) -> None:
    await _limit_request(
        limiter,
        "login",
        _client_ip(request),
        settings.rate_limit_login,
        fail_closed=True,
    )


async def limit_registration(request: Request, limiter: RateLimiterDep) -> None:
    await _limit_request(
        limiter,
        "register",
        _client_ip(request),
        settings.rate_limit_register,
        fail_closed=True,
    )


async def limit_refresh(request: Request, limiter: RateLimiterDep) -> None:
    await _limit_request(
        limiter, "refresh", _client_ip(request), settings.rate_limit_refresh
    )


async def limit_user_requests(limiter: RateLimiterDep, user: CurrentUser) -> None:
    await _limit_request(limiter, "user", str(user.id), settings.rate_limit_user)
