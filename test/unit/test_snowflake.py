from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.settings import settings
from app.utils.snowflake_id import (
    BASE_EPOCH,
    generate_snowflake_id,
    generate_snowflake_id_in_base_epoch,
)


def test_snowflake_generation():
    id = generate_snowflake_id()
    machine_id = (id >> 12) & 0b1111111111
    id_time = datetime.fromtimestamp((id >> (10 + 12)) / 1000.0, UTC)

    assert machine_id == settings.machine_id
    assert datetime.now(UTC) - id_time < timedelta(seconds=0.01)


def test_snowflake_in_epoch():
    id = generate_snowflake_id_in_base_epoch()
    id_time = (id >> (10 + 12)) / 1000.0

    assert 0.01 >= (datetime.now(UTC) - BASE_EPOCH).total_seconds() - id_time >= 0.0


def test_snowflake_ids_remain_unique_within_one_clock_tick():
    epoch = datetime(2014, 1, 1, tzinfo=UTC)
    with patch("app.utils.snowflake_id.datetime") as clock:
        clock.now.return_value = datetime(2026, 11, 7, tzinfo=UTC)
        ids = [generate_snowflake_id(epoch) for _ in range(5_000)]
    assert len(ids) == len(set(ids))
