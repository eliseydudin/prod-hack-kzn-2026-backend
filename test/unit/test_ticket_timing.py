from datetime import date, datetime, time

from app.models.ticket import TicketKind
from app.tickets.parser import ParsedBoardingTime, ParsedPlace, ParsedTicket
from app.tickets.timing import prepare_ticket_preview, trip_time_issues


def test_boarding_pass_resolves_year_only_from_a_unique_trip_date():
    parsed = ParsedTicket(
        kind=TicketKind.PLANE,
        places=(ParsedPlace("SVO", iata="SVO"), ParsedPlace("KZN", iata="KZN")),
        started_at_hint=ParsedBoardingTime(month=11, day=5, local_time=time(13, 10)),
    )
    matched = prepare_ticket_preview(parsed, date(2026, 11, 4), date(2026, 11, 6))
    assert matched.ready_to_apply
    assert matched.started_at.isoformat() == "2026-11-05T13:10:00+03:00"
    assert matched.started_at_hint.local_date == date(2026, 11, 5)
    assert matched.ended_at is None

    multiple_years = prepare_ticket_preview(
        parsed, date(2025, 1, 1), date(2027, 12, 31)
    )
    assert not multiple_years.ready_to_apply
    assert multiple_years.started_at is None
    assert multiple_years.started_at_hint.time_zone == "Europe/Moscow"
    assert multiple_years.issues[0].code == "date_required"


def test_ambiguous_airport_time_is_left_for_confirmation():
    parsed = ParsedTicket(
        kind=TicketKind.PLANE,
        places=(ParsedPlace("JFK", iata="JFK"), ParsedPlace("LHR", iata="LHR")),
        started_at_hint=ParsedBoardingTime(month=11, day=1, local_time=time(1, 30)),
    )
    preview = prepare_ticket_preview(parsed, date(2026, 11, 1), date(2026, 11, 2))
    assert preview.started_at is None
    assert preview.started_at_hint.local_date == date(2026, 11, 1)
    assert preview.issues[0].code == "timezone_required"


def test_trip_time_checks_use_utc_days_and_compare_instants():
    start = datetime.fromisoformat("2026-11-05T01:30:00+03:00")
    end = datetime.fromisoformat("2026-11-05T00:30:00-02:00")
    assert trip_time_issues(start, end, date(2026, 11, 4), date(2026, 11, 5)) == []
    issues = trip_time_issues(start, end, date(2026, 11, 5), date(2026, 11, 5))
    assert [(issue.field, issue.code) for issue in issues] == [
        ("startedAt", "outside_trip")
    ]
