from datetime import UTC, datetime, timedelta
from functools import partial
from random import Random
from typing import Optional

from app.settings import settings

RANDOM = Random()


def generate_snowflake_id(epoch: Optional[datetime] = None):
    epoch = epoch or datetime.fromtimestamp(0, UTC)
    assert epoch.tzinfo is not None

    delta = datetime.now(UTC) - epoch
    delta_as_ms = int(delta / timedelta(milliseconds=1))
    delta_limited = delta_as_ms & 0b11111111111111111111111111111111111111111

    unique_id = RANDOM.randint(0, 2**12 - 1)

    return (delta_limited << (10 + 12)) | (settings.machine_id << 12) | unique_id


BASE_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)
generate_snowflake_id_in_base_epoch = partial(generate_snowflake_id, epoch=BASE_EPOCH)
