from unittest.mock import Mock

from pytest import MonkeyPatch

from app.models.ticket import TicketKind
from app.tickets import geocoding
from app.tickets.parser import ParsedPlace


def _result(osm_key: str, osm_value: str, latitude: float) -> dict:
    return {
        "properties": {
            "name": "Rowes Wharf Terminal",
            "city": "Boston",
            "osm_key": osm_key,
            "osm_value": osm_value,
        },
        "geometry": {"type": "Point", "coordinates": [-71, latitude]},
    }


async def test_geocoder_ignores_wrong_kind_and_requires_unique_match(
    monkeypatch: MonkeyPatch,
):
    results = [
        _result("highway", "bus_stop", 42.2),
        _result("amenity", "ferry_terminal", 42.3),
    ]
    monkeypatch.setattr(geocoding, "_photon_results", lambda *_: results)
    place = ParsedPlace(name="Rowes Wharf Terminal, Boston")

    point = await geocoding.resolve_ticket_place(place, TicketKind.SHIP)
    assert point is not None
    assert (point.latitude, point.longitude) == (42.3, -71)

    results.append(_result("amenity", "ferry_terminal", 42.4))
    assert await geocoding.resolve_ticket_place(place, TicketKind.SHIP) is None


async def test_geocoder_validates_address_order_and_coordinates(
    monkeypatch: MonkeyPatch,
):
    place = ParsedPlace("Hotel", "2-я улица, 7, Москва")
    bad_address = _result("building", "residential", 55.1)
    bad_address["properties"].update(street="7-я улица", housenumber="2", city="Москва")
    bad_coordinates = _result("building", "residential", 55.2)
    bad_coordinates["properties"].update(
        street="2-я улица", housenumber="7", city="Москва"
    )
    bad_coordinates["geometry"]["coordinates"] = [False, True]
    lookup = Mock(return_value=[bad_address, bad_coordinates])
    monkeypatch.setattr(geocoding, "_photon_results", lookup)

    assert await geocoding.resolve_ticket_place(place, TicketKind.STAY) is None
    assert (
        await geocoding.resolve_ticket_place(
            ParsedPlace("Hotel", "Москва; alice@example.com"), TicketKind.STAY
        )
        is None
    )
    assert (
        await geocoding.resolve_ticket_place(
            ParsedPlace("Hotel", "Москва; +7 (900) 000-00-00"), TicketKind.STAY
        )
        is None
    )
    assert (
        await geocoding.resolve_ticket_place(
            ParsedPlace("Hotel", "Paris Guest Ada Lovelace confirmation 123"),
            TicketKind.STAY,
        )
        is None
    )
    assert (
        await geocoding.resolve_ticket_place(
            ParsedPlace("Paris Charles de Gaulle", iata="LHR"), TicketKind.PLANE
        )
        is None
    )
    assert (
        await geocoding.resolve_ticket_place(
            ParsedPlace("MOW", iata="MOW"), TicketKind.PLANE
        )
        is None
    )
    lookup.assert_called_once()
