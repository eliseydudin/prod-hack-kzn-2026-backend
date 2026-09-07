from datetime import UTC, datetime, timedelta

from app.settings import settings
from app.utils.snowflake_id import generate_snowflake_id


def test_snowflake_generation():
    id = generate_snowflake_id()
    machine_id = (id >> 12) & 0b1111111111
    time_ms = datetime.fromtimestamp((id >> (10 + 12)) / 1000.0, UTC)

    assert machine_id == settings.machine_id
    assert datetime.now(UTC) - time_ms < timedelta(seconds=0.01)
