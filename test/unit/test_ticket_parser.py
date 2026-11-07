from datetime import datetime

import pytest

from app.models.ticket import TicketKind
from app.tickets.parser import ParsedPlace, TicketParseError, parse_ticket


def test_flight_reads_iata_and_explicit_timestamps_without_passenger_data():
    ticket = parse_ticket(
        "Электронный авиабилет\n"
        "Вылет: 2026-11-07T07:20+03:00 Москва Шереметьево (SVO) "
        "Прилёт: Казань (KZN) Пассажир: Иван Иванов\n"
        "Время прилёта: 07.11.2026 08:50 +03:00"
    )

    assert ticket.kind == TicketKind.PLANE
    assert ticket.places == (
        ParsedPlace("Москва Шереметьево", iata="SVO"),
        ParsedPlace("Казань", iata="KZN"),
    )
    assert ticket.started_at == datetime.fromisoformat("2026-11-07T07:20+03:00")
    assert ticket.ended_at == datetime.fromisoformat("2026-11-07T08:50+03:00")


def test_private_fields_are_not_locations_and_hotel_time_stays_local():
    hotel = parse_ticket(
        "Hotel: Astoria\nBilling address: 10 Downing Street, London\n"
        "Адрес гостя: Москва\nCheck-in: 2026-12-10T15:00+03:00"
    )
    assert hotel.places == (ParsedPlace("Astoria"),)
    assert hotel.started_at == datetime(2026, 12, 10, 15)
    rail = parse_ticket(
        "Train ticket\nFrom: London Paddington Passenger Name Ada Lovelace\n"
        "To: Oxford\nDeparture time: 2026-12-10T12:00+03:99"
    )
    assert rail.places == (ParsedPlace("London Paddington"), ParsedPlace("Oxford"))
    assert rail.started_at is None


@pytest.mark.parametrize(
    ("text", "kind", "places"),
    [
        (
            "Flight itinerary\nCheck-in at the terminal\nRoute: SVO → KZN",
            TicketKind.PLANE,
            (ParsedPlace("SVO", iata="SVO"), ParsedPlace("KZN", iata="KZN")),
        ),
        (
            (
                "Электронный билет. Поезд №42\n"
                "Станция отправления      Станция прибытия\n"
                "Москва Казанский вокзал      Казань-Пассажирская"
            ),
            TicketKind.TRAIN,
            (
                ParsedPlace("Москва Казанский вокзал"),
                ParsedPlace("Казань-Пассажирская"),
            ),
        ),
        (
            (
                "Ferry confirmation\nEmbarkation port:\nHelsinki West Harbour\n"
                "Port of disembarkation: Tallinn Old City Harbour"
            ),
            TicketKind.SHIP,
            (
                ParsedPlace("Helsinki West Harbour"),
                ParsedPlace("Tallinn Old City Harbour"),
            ),
        ),
        (
            "Билет на корабль\nПорт отправления: Сочи\nПорт прибытия: Гагра",
            TicketKind.SHIP,
            (ParsedPlace("Сочи"), ParsedPlace("Гагра")),
        ),
        (
            (
                "Hotel booking confirmation\nGuest name: Ada Lovelace\n"
                "Hotel: Astoria Hotel\nHotel address: 39 Bolshaya Morskaya, Saint Petersburg"
            ),
            TicketKind.STAY,
            (ParsedPlace("Astoria Hotel", "39 Bolshaya Morskaya, Saint Petersburg"),),
        ),
    ],
)
def test_parse_ticket_formats(text, kind, places):
    ticket = parse_ticket(text)
    assert (ticket.kind, ticket.places) == (kind, places)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Flight itinerary\nSVO-KZN\nKZN-LED", "ticket_route_ambiguous"),
        (
            "Train\nDeparture:\nPassenger: Ada Lovelace\nArrival: London Paddington",
            "ticket_route_not_found",
        ),
    ],
)
def test_parse_ticket_rejects_unusable_routes(text, reason):
    with pytest.raises(TicketParseError, match=reason):
        parse_ticket(text)
