from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Lock
from typing import Optional

from app.settings import settings

_lock = Lock()
_state: dict[tuple[datetime, int], tuple[int, int]] = {}


def generate_snowflake_id(epoch: Optional[datetime] = None):
    epoch = epoch or datetime.fromtimestamp(0, UTC)
    if epoch.utcoffset() is None:
        raise ValueError("epoch must include a timezone")
    machine_id = settings.machine_id
    with _lock:
        milliseconds = (datetime.now(UTC) - epoch) // timedelta(milliseconds=1)
        previous, sequence = _state.get((epoch, machine_id), (-1, -1))
        if milliseconds <= previous:
            milliseconds, sequence = previous, sequence + 1
            if sequence == 2**12:
                milliseconds, sequence = milliseconds + 1, 0
        else:
            sequence = 0
        if not 0 <= milliseconds < 2**41:
            raise ValueError("timestamp is outside the Snowflake epoch range")
        _state[(epoch, machine_id)] = (milliseconds, sequence)
        return (milliseconds << 22) | (machine_id << 12) | sequence


BASE_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)
generate_snowflake_id_in_base_epoch = partial(generate_snowflake_id, epoch=BASE_EPOCH)
