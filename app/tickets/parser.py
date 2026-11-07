import re
from collections import defaultdict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import datetime
from unicodedata import normalize

from app.models.ticket import TicketKind

_DEPARTURE = (
    r"departure(?: airport| station| port| location)?|departs?|origin|from"
    r"|(?:port of )?embarkation(?: port)?"
    r"|(?:пункт |станция |порт )отправления|аэропорт вылета"
    r"|отправление|вылет|посадка в порту|откуда|из"
)
_ARRIVAL = (
    r"arrival(?: airport| station| port| location)?|arrives?|destination|to"
    r"|(?:port of )?disembarkation(?: port)?"
    r"|(?:пункт |станция |порт )прибытия|аэропорт прил[её]та"
    r"|прибытие|прил[её]т|высадка в порту|куда"
)
_LABELS = {
    "private": (
        r"(?:guest|passenger|travell?er|billing|payment|home|contact) address"
        r"|(?:guest|passenger|travell?er) name|(?:booking|reservation) (?:reference|number|id)|pnr"
        r"|адрес (?:гостя|пассажира|плательщика|проживания|регистрации)"
        r"|(?:плат[её]жный|домашний|контактный) адрес|фио (?:гостя|пассажира)"
        r"|номер (?:бронирования|брони|заказа)"
    ),
    "departure_time": r"departure (?:date(?: and time)?|time)|(?:дата|время) (?:вылета|отправления)",
    "arrival_time": r"arrival (?:date(?: and time)?|time)|(?:дата|время) (?:прил[её]та|прибытия)",
    "departure_address": r"departure address|адрес (?:отправления|вылета)",
    "arrival_address": r"arrival address|адрес (?:прибытия|прил[её]та)",
    "departure": _DEPARTURE,
    "arrival": _ARRIVAL,
    "address": r"(?:hotel |property |accommodation )?address|адрес(?: отеля| гостиницы| объекта)?",
    "hotel": r"(?:hotel|property|accommodation)(?: name)?|(?:название )?(?:отель|гостиница|объект размещения)",
    "check_in": r"check[ -]?in(?: date| time)?|дата заезда|заезд|заселение",
    "check_out": r"check[ -]?out(?: date| time)?|дата выезда|выезд|выселение",
    "ignored": (
        r"passenger(?: name)?|guest(?: name)?|traveler(?: name)?|surname|first name"
        r"|booking (?:reference|number|id)|reservation (?:number|id)|pnr"
        r"|ticket (?:number|no\.?)|flight(?: number| no\.?)?|train(?: number| no\.?)?"
        r"|seat|carriage|gate|platform|пассажир|гость|ф\.?и\.?о\.?|фамилия|имя"
        r"|номер (?:брони|бронирования|билета|рейса|поезда)|рейс|поезд|вагон|место"
    ),
}
_LABEL = re.compile(
    r"(?<!\w)(?:"
    + "|".join(f"(?P<{name}>{label})" for name, label in _LABELS.items())
    + r")(?!\w)",
    re.IGNORECASE,
)
_CONTACT_DATA = re.compile(
    r"@|://|(?<!\d)\d(?:[ ().+-]*\d){9}"
    r"|\b(?:booking|confirmation|reservation|подтверждение)\s+[#№]?\s*[A-Z0-9]*\d",
    re.IGNORECASE,
)
_COLUMNS = re.compile(r"\t+| {2,}|\s*\|\s*")
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
_ZONE = r"Z|(?:UTC[ \t]*)?[+-]\d{2}:?\d{2}"
_DATE = r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}"
_DATE_TIME = re.compile(
    rf"(?<!\d)(?P<date>{_DATE})[T \t]+(?P<time>{_TIME})(?:[ \t]*(?P<zone>{_ZONE}))?(?![\d:])",
    re.IGNORECASE,
)
_DATE_OR_TIME = re.compile(
    rf"(?<!\d)(?:{_DATE}|{_TIME}(?:[ \t]*(?:{_ZONE}))?)(?!\d)", re.IGNORECASE
)
_KIND_MARKERS = {
    TicketKind.PLANE: r"\b(?:flight|airline|airport|aviation|авиабилет\w*|авиакомпани\w*|аэропорт\w*|вылет\w*|прил[её]т\w*)\b",
    TicketKind.TRAIN: (
        r"\b(?:train|railway|rail ticket|rail station|поезд\w*|вагон\w*"
        r"|железнодорож\w*|ржд|ж[/.-]?д)\b"
    ),
    TicketKind.SHIP: (
        r"\b(?:ferry|cruise|vessel|ship|embarkation|disembarkation"
        r"|паром\w*|теплоход\w*|корабл\w*|круиз\w*|судно|судна"
        r"|морской порт|порт отправления|порт прибытия)\b"
    ),
    TicketKind.STAY: r"\b(?:hotel|accommodation|check[ -]?in|check[ -]?out|отель|отеля|гостиниц\w*|заселени\w*|заезд)\b",
}
_ARROW = re.compile(r"\s*(?:→|->|⇒)\s*")
_ROUTE_HEADING = re.compile(r"^(?:route|маршрут)[ \t]*[:=][ \t]*", re.IGNORECASE)
_IATA = re.compile(r"\b[A-Z]{3}\b")
_IATA_PAREN = re.compile(r"\s*\(([A-Z]{3})\)\s*")
_IATA_PAIR = re.compile(r"([A-Z]{3})(?:\s*(?:[-–—/→⇒]|->)\s*|[ \t]{2,})([A-Z]{3})")


@dataclass(frozen=True, slots=True)
class ParsedPlace:
    name: str
    address: str | None = None
    iata: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedTicket:
    kind: TicketKind
    places: tuple[ParsedPlace, ...]
    started_at: datetime | None = None
    ended_at: datetime | None = None


class TicketParseError(ValueError):
    pass


def _labels(line: str) -> list[re.Match[str]]:
    return [
        match
        for match in _LABEL.finditer(line)
        if match.lastgroup == "private"
        or line[match.end() :].lstrip().startswith((":", "="))
        or not match.start()
        or line[: match.start()].endswith(("  ", "\t", "|", ";"))
    ]


def has_private_place_data(value: str) -> bool:
    return _CONTACT_DATA.search(value) is not None or any(
        label.lastgroup == "private" for label in _LABEL.finditer(value)
    )


def _fields(lines: list[str]) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = defaultdict(list)
    for index, line in enumerate(lines):
        labels = _labels(line)
        if not labels:
            continue
        values = [
            line[label.end() : end].strip(" :=;|\t")
            for label, end in zip(
                labels, [label.start() for label in labels[1:]] + [len(line)]
            )
        ]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if next_line and not _labels(next_line):
            if len(labels) == 1 and not _without_time(values[0]).strip():
                values[0] = f"{values[0]} {next_line}".strip()
            elif not any(values):
                columns = _COLUMNS.split(next_line)
                if len(columns) == len(labels):
                    values = columns
        for label, value in zip(labels, values):
            if value and label.lastgroup not in {"ignored", "private"}:
                fields[label.lastgroup].append(value)
    return fields


def _compact(value: str) -> str:
    return " ".join(value.split()).strip(" ,;:=|–—-→⇒>")


def _without_time(value: str) -> str:
    return _DATE_OR_TIME.sub(" ", _DATE_TIME.sub(" ", value))


def _place(value: str, kind: TicketKind) -> ParsedPlace | None:
    name = _compact(_without_time(value))
    if not name or len(name) > 128 or not any(char.isalpha() for char in name):
        return None
    if kind != TicketKind.PLANE:
        return ParsedPlace(name)
    if _IATA.fullmatch(name):
        return ParsedPlace(name, iata=name)
    codes = set(_IATA_PAREN.findall(name))
    if len(codes) > 1:
        raise TicketParseError("ticket_route_ambiguous")
    if codes:
        code = codes.pop()
        return ParsedPlace(_compact(_IATA_PAREN.sub(" ", name)) or code, iata=code)
    return ParsedPlace(name)


def _date_time(values: list[str], *, local: bool = False) -> datetime | None:
    moments = set()
    candidates = [*values, " ".join(values)] if len(values) == 2 else values
    for value in candidates:
        for match in _DATE_TIME.finditer(value):
            day = match["date"]
            if "." in day:
                day = "-".join(reversed(day.split(".")))
            hour, time = match["time"].split(":", 1)
            zone = (match["zone"] or "").upper().removeprefix("UTC").strip()
            if zone and zone != "Z" and int(zone[-2:]) >= 60:
                continue
            try:
                moment = datetime.fromisoformat(f"{day}T{hour.zfill(2)}:{time}{zone}")
            except ValueError:
                continue
            moments.add(moment.replace(tzinfo=None) if local else moment)
    return moments.pop() if len(moments) == 1 else None


def _identity(place: ParsedPlace) -> str:
    return place.iata or place.name.casefold().replace("ё", "е")


def _only[T](values: list[T], key: Callable[[T], Hashable], error: str) -> T | None:
    unique = {key(value): value for value in values}
    if len(unique) > 1:
        raise TicketParseError(error)
    return next(iter(unique.values()), None)


def _address(values: list[str], error: str) -> str | None:
    return _only(
        [value for raw in values if 0 < len(value := _compact(raw)) <= 512],
        str.casefold,
        error,
    )


def _route_candidates(
    lines: list[str], kind: TicketKind
) -> list[tuple[ParsedPlace, ParsedPlace]]:
    routes = []
    named_airports = []
    for line in lines:
        line = _ROUTE_HEADING.sub("", line)
        if _labels(line):
            continue
        parts = _ARROW.split(line)
        if len(parts) > 2:
            raise TicketParseError("ticket_route_ambiguous")
        if len(parts) == 1 and kind == TicketKind.PLANE:
            if pair := _IATA_PAIR.fullmatch(line):
                parts = [pair[1], pair[2]]
            elif _IATA_PAREN.search(line) and (place := _place(line, kind)):
                named_airports.append(place)
        if len(parts) == 2:
            left, right = (_place(part, kind) for part in parts)
            if left and right:
                routes.append((left, right))
    if len(named_airports) > 2:
        raise TicketParseError("ticket_route_ambiguous")
    if len(named_airports) == 2:
        routes.append((named_airports[0], named_airports[1]))
    return routes


def _kind(
    text: str, lines: list[str], fields: dict[str, list[str]], hint: TicketKind | None
) -> TicketKind:
    kinds = {
        kind
        for kind, pattern in _KIND_MARKERS.items()
        if re.search(pattern, text, re.IGNORECASE)
    }
    if not kinds and any(
        _IATA_PAIR.fullmatch(_ROUTE_HEADING.sub("", line)) for line in lines
    ):
        kinds.add(TicketKind.PLANE)
    if (
        not fields.get("hotel")
        and not fields.get("address")
        and kinds - {TicketKind.STAY}
    ):
        kinds.discard(TicketKind.STAY)
    elif (
        not (fields.get("departure") or fields.get("arrival"))
        and TicketKind.STAY in kinds
        and (fields.get("hotel") or fields.get("address"))
    ):
        kinds = {TicketKind.STAY}
    if hint is not None:
        if kinds and hint not in kinds:
            raise TicketParseError("ticket_kind_mismatch")
        return hint
    if not kinds:
        raise TicketParseError("ticket_kind_not_found")
    if len(kinds) > 1:
        raise TicketParseError("ticket_kind_ambiguous")
    return kinds.pop()


def _hotel(fields: dict[str, list[str]]) -> ParsedTicket:
    error = "hotel_location_ambiguous"
    names = [
        place.name
        for raw in fields["hotel"]
        if not re.match(
            r"^(?:booking|reservation|confirmation|voucher|бронирование)\b",
            raw,
            re.IGNORECASE,
        )
        if (place := _place(raw, TicketKind.STAY))
    ]
    name = _only(names, str.casefold, error)
    address = _address(fields["address"], error)
    name = name or (address[:128] if address else None)
    if not name:
        raise TicketParseError("hotel_location_not_found")
    return ParsedTicket(
        TicketKind.STAY,
        (ParsedPlace(name, address),),
        _date_time(fields["check_in"], local=True),
        _date_time(fields["check_out"], local=True),
    )


def _transport(
    kind: TicketKind, lines: list[str], fields: dict[str, list[str]]
) -> ParsedTicket:
    error = "ticket_route_ambiguous"
    locations = [
        _only(
            [place for raw in fields[role] if (place := _place(raw, kind))],
            _identity,
            error,
        )
        for role in ("departure", "arrival")
    ]
    routes = _route_candidates(lines, kind)
    departure, arrival = locations
    if departure and arrival:
        routes.append((departure, arrival))
    route = _only(routes, lambda pair: tuple(map(_identity, pair)), error)
    if route is None or _identity(route[0]) == _identity(route[1]):
        raise TicketParseError("ticket_route_not_found")
    for explicit, fallback in zip(locations, route):
        if explicit and _identity(explicit) != _identity(fallback):
            raise TicketParseError(error)
    points = tuple(
        ParsedPlace(point.name, _address(fields[f"{role}_address"], error), point.iata)
        for point, role in zip(route, ("departure", "arrival"))
    )
    return ParsedTicket(
        kind,
        points,
        _date_time(fields["departure"] + fields["departure_time"]),
        _date_time(fields["arrival"] + fields["arrival_time"]),
    )


def parse_ticket(text: str, kind_hint: TicketKind | None = None) -> ParsedTicket:
    text = normalize("NFKC", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fields = _fields(lines)
    kind = _kind(text, lines, fields, kind_hint)
    if kind == TicketKind.STAY:
        return _hotel(fields)
    return _transport(kind, lines, fields)
