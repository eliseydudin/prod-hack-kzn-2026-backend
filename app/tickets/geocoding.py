import re
from functools import cache, lru_cache
from http.client import HTTPException
from json import loads
from unicodedata import combining, normalize
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import airportsdata
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.models.ticket import TicketKind, TicketPoint
from app.settings import settings
from app.tickets.parser import ParsedPlace, has_private_place_data

_TAGS = {
    TicketKind.PLANE: {"aeroway:aerodrome", "aeroway:terminal"},
    TicketKind.TRAIN: {"railway:station", "railway:halt", "building:train_station"},
    TicketKind.SHIP: {
        "amenity:ferry_terminal",
        "building:ferry_terminal",
        "harbour:yes",
        "industrial:port",
        "landuse:port",
        "man_made:pier",
        "waterway:dock",
    },
    TicketKind.STAY: {
        "tourism:hotel",
        "tourism:hostel",
        "tourism:motel",
        "tourism:guest_house",
        "tourism:apartment",
    },
}
_LOCALITY_FIELDS = ("locality", "district", "city", "county", "state", "country")
_NAME_WORDS = frozenset(
    [
        "airport",
        "aerodrome",
        "international",
        "аэропорт",
        "международный",
        "station",
        "railway",
        "train",
        "вокзал",
        "станция",
        "железнодорожный",
        "жд",
        "terminal",
        "ferry",
        "port",
        "harbour",
        "harbor",
        "pier",
        "терминал",
        "паром",
        "порт",
        "причал",
        "hotel",
        "hostel",
        "motel",
        "гостиница",
        "отель",
        "хостел",
        "de",
        "du",
        "des",
        "la",
        "le",
        "of",
        "the",
    ]
)
_ADDRESS_WORDS = {"г", "город", "д", "дом", "city"}
_WORD_ALIASES = {"ул": "улица", "просп": "проспект"}
_RESULT_LIMIT = 8
_RESPONSE_LIMIT = 64 * 1024


@cache
def _iata_airports():
    return airportsdata.load("IATA")


def _airport_point(place: ParsedPlace, iata: str) -> TicketPoint | None:
    airport = _iata_airports().get(iata)
    if airport is None:
        return None
    if place.name.upper() != iata and place.name.isascii():
        given_name = _words(place.name) - _NAME_WORDS
        city = _words(airport["city"])
        airport_name = _words(airport["name"]) - _NAME_WORDS - city
        if not (given_name <= city or given_name & airport_name):
            return None
    address = _join_address(
        airport["name"], airport["city"], airport["subd"], airport["country"]
    )
    return TicketPoint(
        name=airport["name"][:128],
        address=address[:512],
        latitude=airport["lat"],
        longitude=airport["lon"],
    )


@lru_cache(maxsize=256)
def _photon_results(base_url: str, query: str) -> list:
    params = urlencode({"q": query, "limit": _RESULT_LIMIT})
    request = Request(
        f"{base_url.rstrip('/')}/api/?{params}",
        headers={"Accept": "application/json", "User-Agent": "TravelHub ticket import"},
    )
    with urlopen(request, timeout=3) as response:
        body = response.read(_RESPONSE_LIMIT + 1)
    if len(body) > _RESPONSE_LIMIT:
        raise ValueError("geocoder response is too large")
    data = loads(body)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise TypeError("invalid geocoder response")
    return features[:_RESULT_LIMIT]


def _tokens(text: str) -> tuple[str, ...]:
    folded = normalize("NFKD", text.casefold())
    words = re.findall(r"[^\W_]+", "".join(c for c in folded if not combining(c)))
    return tuple(
        _WORD_ALIASES.get(word, word) for word in words if word not in _ADDRESS_WORDS
    )


def _words(text: str) -> set[str]:
    return set(_tokens(text))


def _matches_street_number(query: str, street: str, house: str) -> bool:
    street_tokens, house_tokens = _tokens(street), _tokens(house)
    if not street_tokens or not house_tokens:
        return False
    tokens = _tokens(query)
    length = len(street_tokens) + len(house_tokens)
    orders = (street_tokens + house_tokens, house_tokens + street_tokens)
    return any(
        tokens[start : start + length] in orders
        for start in range(len(tokens) - length + 1)
    )


def _text(properties: dict, key: str) -> str:
    value = properties.get(key)
    return value.strip() if isinstance(value, str) else ""


def _join_address(*parts: str) -> str:
    return ", ".join(dict.fromkeys(part for part in parts if part))


def _candidate(
    feature: object, place: ParsedPlace, kind: TicketKind, query: str
) -> TicketPoint | None:
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if (
        geometry.get("type") != "Point"
        or not isinstance(coordinates, list)
        or len(coordinates) != 2
        or any(type(value) not in (int, float) for value in coordinates)
    ):
        return None

    name = _text(properties, "name")
    street = _text(properties, "street")
    house = _text(properties, "housenumber")
    locality = tuple(_text(properties, field) for field in _LOCALITY_FIELDS)
    address = _join_address(
        " ".join(part for part in (street, house) if part), *locality
    )
    address_words = _words(address + " " + _text(properties, "postcode"))
    query_words = _words(query)

    if place.address:
        if (
            not _matches_street_number(query, street, house)
            or not query_words <= address_words
        ):
            return None
        name = place.name
    else:
        tag = _text(properties, "osm_key") + ":" + _text(properties, "osm_value")
        name_words = _words(name) - _NAME_WORDS
        query_words -= _NAME_WORDS
        if (
            not name_words
            or tag not in _TAGS[kind]
            or not name_words <= query_words <= name_words | address_words
        ):
            return None
        address = address or name

    longitude, latitude = coordinates
    try:
        return TicketPoint(
            name=name[:128],
            address=address[:512],
            latitude=latitude,
            longitude=longitude,
        )
    except ValidationError:
        return None


def _resolve(place: ParsedPlace, kind: TicketKind) -> TicketPoint | None:
    if kind == TicketKind.PLANE and place.iata:
        return _airport_point(place, place.iata.upper())

    query = " ".join((place.address or place.name).split())
    if not query or len(query) > 256 or has_private_place_data(query):
        return None
    try:
        features = _photon_results(str(settings.ticket_geocoder_url), query)
    except HTTPException, OSError, ValueError, TypeError, RecursionError:
        return None
    points = {}
    for feature in features:
        point = _candidate(feature, place, kind, query)
        if point is not None:
            points.setdefault(
                (round(point.latitude, 4), round(point.longitude, 4)), point
            )
    return next(iter(points.values())) if len(points) == 1 else None


async def resolve_ticket_place(
    place: ParsedPlace, kind: TicketKind
) -> TicketPoint | None:
    return await run_in_threadpool(_resolve, place, kind)
