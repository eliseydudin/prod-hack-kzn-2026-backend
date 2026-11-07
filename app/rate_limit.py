from asyncio import timeout
from logging import getLogger

from fastapi import Request
from valkey.asyncio import Valkey
from valkey.asyncio.retry import Retry
from valkey.backoff import NoBackoff
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from app.auth.tokens import CurrentUser
from app.responses import RateLimitError, ServiceUnavailableError
from app.settings import settings

logger = getLogger(__name__)

VALKEY_TIMEOUT = 0.25
FIXED_WINDOW = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
return {count, redis.call("PTTL", KEYS[1])}
"""


def create_rate_limit_client(url: str) -> Valkey:
    return Valkey.from_url(
        url,
        socket_connect_timeout=VALKEY_TIMEOUT,
        socket_timeout=VALKEY_TIMEOUT,
        retry=Retry(NoBackoff(), 0),
    )


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
        except (TimeoutError, ValkeyConnectionError, ValkeyTimeoutError) as error:
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


async def _limit_request(
    request: Request,
    policy: str,
    subject: str,
    limit: int,
    *,
    fail_closed: bool = False,
) -> None:
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        await limiter.enforce(policy, subject, limit, fail_closed=fail_closed)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def limit_login(request: Request) -> None:
    await _limit_request(
        request,
        "login",
        _client_ip(request),
        settings.rate_limit_login,
        fail_closed=True,
    )


async def limit_registration(request: Request) -> None:
    await _limit_request(
        request,
        "register",
        _client_ip(request),
        settings.rate_limit_register,
        fail_closed=True,
    )


async def limit_refresh(request: Request) -> None:
    await _limit_request(
        request, "refresh", _client_ip(request), settings.rate_limit_refresh
    )


async def limit_user_requests(request: Request, user: CurrentUser) -> None:
    await _limit_request(request, "user", str(user.id), settings.rate_limit_user)
