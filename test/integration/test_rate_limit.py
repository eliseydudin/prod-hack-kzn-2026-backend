from asyncio import gather, sleep, timeout
from os import getenv
from uuid import uuid4

import pytest

from app.rate_limit import RateLimiter, create_rate_limit_client
from app.responses import RateLimitError


async def test_parallel_requests_share_a_quota_and_rejections_do_not_renew_it():
    url = getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL must point to a test Valkey instance")

    subject = uuid4().hex
    async with (
        create_rate_limit_client(url) as first,
        create_rate_limit_client(url) as second,
    ):
        limiters = [RateLimiter(first, 1), RateLimiter(second, 1)]
        results = await gather(
            *(limiters[index % 2].enforce("test", subject, 3) for index in range(12)),
            return_exceptions=True,
        )

        assert results.count(None) == 3
        assert sum(isinstance(result, RateLimitError) for result in results) == 9
        await limiters[0].enforce("test", uuid4().hex, 3)

        async with timeout(3):
            while True:
                try:
                    await limiters[0].enforce("test", subject, 3)
                except RateLimitError:
                    await sleep(0.05)
                else:
                    break
