from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.ticket import TicketIssue, TicketPreview, TicketTimeHint
from app.tickets.geocoding import airport_timezone
from app.tickets.parser import ParsedBoardingTime, ParsedTicket


def trip_time_issues(
    started_at: datetime | None,
    ended_at: datetime | None,
    trip_start: date,
    trip_end: date | None,
) -> list[TicketIssue]:
    issues = []
    start = started_at.astimezone(UTC) if started_at is not None else None
    end = ended_at.astimezone(UTC) if ended_at is not None else None
    if start is not None and end is not None and end < start:
        issues.append(
            TicketIssue(
                field="endedAt",
                code="ended_before_start",
                msg="endedAt must be on or after startedAt",
            )
        )
    for field, value in (("startedAt", start), ("endedAt", end)):
        if value is None:
            continue
        day = value.date()
        if day < trip_start or (trip_end is not None and day > trip_end):
            issues.append(
                TicketIssue(
                    field=field,
                    code="outside_trip",
                    msg=f"{field} is outside the trip dates in UTC",
                )
            )
    return issues


def _boarding_date(
    hint: ParsedBoardingTime, first: date, last: date | None
) -> date | None:
    if last is None:
        return None
    matched = None
    for year in range(first.year, last.year + 1):
        try:
            candidate = date(year, hint.month, hint.day)
        except ValueError:
            continue
        if first <= candidate <= last:
            if matched is not None:
                return None
            matched = candidate
    return matched


def _airport_time(value: datetime, zone_name: str) -> datetime | None:
    try:
        zone = ZoneInfo(zone_name)
        matches = {}
        for fold in (0, 1):
            local = value.replace(tzinfo=zone, fold=fold)
            utc = local.astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == value:
                matches[utc] = local
        return next(iter(matches.values())) if len(matches) == 1 else None
    except ZoneInfoNotFoundError, OverflowError:
        return None


def _time_hint(value: datetime, zone_name: str | None) -> TicketTimeHint:
    return TicketTimeHint(
        month=value.month,
        day=value.day,
        local_date=value.date(),
        local_time=value.time(),
        time_zone=zone_name,
    )


def prepare_ticket_preview(
    parsed: ParsedTicket, trip_start: date, trip_end: date | None
) -> TicketPreview:
    started_at, ended_at = parsed.started_at, parsed.ended_at
    started_hint = ended_hint = None
    issues = []
    if hint := parsed.started_at_hint:
        travel_date = _boarding_date(hint, trip_start, trip_end)
        zone_name = airport_timezone(parsed.places[0].iata)
        started_hint = TicketTimeHint(
            month=hint.month,
            day=hint.day,
            local_date=travel_date,
            local_time=hint.local_time,
            time_zone=zone_name,
        )
        if travel_date is not None and zone_name:
            started_at = _airport_time(
                datetime.combine(travel_date, hint.local_time), zone_name
            )
        if started_at is None:
            issues.append(
                TicketIssue(
                    field="startedAt",
                    code="date_required"
                    if travel_date is None
                    else "timezone_required",
                    msg="Confirm the departure date and time zone",
                )
            )
    elif started_at is None:
        issues.append(
            TicketIssue(
                field="startedAt",
                code="start_required",
                msg="Enter a start time with its UTC offset",
            )
        )
    elif started_at.utcoffset() is None:
        started_hint = _time_hint(started_at, airport_timezone(parsed.places[0].iata))
        started_at = None
        issues.append(
            TicketIssue(
                field="startedAt",
                code="timezone_required",
                msg="Confirm the start time zone",
            )
        )

    if ended_at is not None and ended_at.utcoffset() is None:
        ended_hint = _time_hint(ended_at, airport_timezone(parsed.places[-1].iata))
        ended_at = None
        issues.append(
            TicketIssue(
                field="endedAt",
                code="timezone_required",
                msg="Confirm the end time zone",
            )
        )

    issues.extend(trip_time_issues(started_at, ended_at, trip_start, trip_end))
    return TicketPreview(
        kind=parsed.kind,
        started_at=started_at,
        ended_at=ended_at,
        started_at_hint=started_hint,
        ended_at_hint=ended_hint,
        issues=issues,
    )
