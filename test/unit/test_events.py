from datetime import UTC, date, datetime

import pytest

from app.events.crud import validate_trip_dates
from app.models.event import EventBase, EventType
from app.models.trip import TripTable
from app.responses import ValidationError


def test_event_date_range_uses_the_same_utc_day_before_and_after_persistence():
    trip = TripTable(
        name="Казань", started_on=date(2026, 11, 7), ended_on=date(2026, 11, 8)
    )
    event = EventBase(
        title="Экскурсия",
        type=EventType.TOUR,
        started_at=datetime.fromisoformat("2026-11-06T23:30:00-02:00"),
        ended_at=datetime.fromisoformat("2026-11-09T00:30:00+02:00"),
    )
    assert event.started_at == datetime(2026, 11, 7, 1, 30, tzinfo=UTC)
    assert event.ended_at == datetime(2026, 11, 8, 22, 30, tzinfo=UTC)
    validate_trip_dates(trip, event)
    with pytest.raises(ValidationError):
        validate_trip_dates(
            trip,
            event.model_copy(update={"ended_at": datetime(2026, 11, 9, tzinfo=UTC)}),
        )
