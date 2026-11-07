from datetime import datetime, time

import pytest

from app.models.ticket import TicketKind
from app.tickets.parser import (
    ParsedBoardingTime,
    ParsedPlace,
    TicketParseError,
    parse_ticket,
)


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


def test_separate_ticket_date_and_time_are_combined_without_guessing():
    ticket = parse_ticket(
        "Flight\nFrom: SVO\nTo: KZN\nDeparture time: 15:00+03:00\n"
        "Departure date: 2026-12-10\nArrival date: 11.12.2026\n"
        "Arrival time: 01:15+03:00"
    )
    assert ticket.started_at == datetime.fromisoformat("2026-12-10T15:00:00+03:00")
    assert ticket.ended_at == datetime.fromisoformat("2026-12-11T01:15:00+03:00")
    ambiguous = parse_ticket(
        "Flight\nFrom: SVO\nTo: KZN\nDeparture date: 2026-12-10\n"
        "Departure date: 2026-12-11\nDeparture time: 15:00+03:00"
    )
    assert ambiguous.started_at is None


def test_boarding_pass_reads_left_route_and_departure_from_repeated_coupons():
    def columns(*values: str) -> str:
        return "    " + "".join(value.ljust(30) for value in values).rstrip()

    coupon = "\n".join(
        (
            columns("From / От", "To / До", "To / До"),
            columns("MOSCOW", "KAZAN / KZN", "KAZAN / KZN"),
            columns("SHEREMETYEVO / SVO", "", "BAGGAGE"),
            columns("Carrier/Flight /", "Class/Date /", "Depart time /", "Flight /"),
            columns("Перевозчик/Рейс", "Класс/Дата", "Время вылета", "13:10"),
            columns("XX 100", "G 05 NOV", "13:10", "Boarding time /"),
            columns("Boarding time /", "12:30–12:50"),
        )
    )

    ticket = parse_ticket(f"{coupon}\n{coupon}")

    assert ticket.kind == TicketKind.PLANE
    assert ticket.places == (
        ParsedPlace("MOSCOW SHEREMETYEVO", iata="SVO"),
        ParsedPlace("KAZAN", iata="KZN"),
    )
    assert ticket.started_at_hint == ParsedBoardingTime(11, 5, time(13, 10))
    assert ticket.started_at is ticket.ended_at is None

    different_route = coupon.replace("KAZAN / KZN", "SOCHI / AER")
    with pytest.raises(TicketParseError, match="ticket_route_ambiguous"):
        parse_ticket(f"{coupon}\n{different_route}")


def test_private_fields_are_not_locations_and_hotel_offset_is_preserved():
    hotel = parse_ticket(
        "Hotel: Astoria\nBilling address: 10 Downing Street, London\n"
        "Адрес гостя: Москва\nCheck-in: 2026-12-10T15:00+03:00"
    )
    assert hotel.places == (ParsedPlace("Astoria"),)
    assert hotel.started_at == datetime.fromisoformat("2026-12-10T15:00+03:00")
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
