from datetime import UTC, datetime


def _point(name: str, address: str, latitude: float, longitude: float) -> dict:
    return {
        "name": name,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
    }


APPLY_EXAMPLES = {
    "plane": {
        "summary": "Авиабилет: SVO → KZN",
        "description": "Демонстрационные данные. Нужна поездка, включающая 10 декабря 2026 года.",
        "value": {
            "kind": "plane",
            "departure": _point(
                "Аэропорт SVO", "Аэропорт Шереметьево, Москва", 55.9726, 37.4146
            ),
            "arrival": _point("Аэропорт KZN", "Аэропорт Казань", 55.6062, 49.2787),
            "startedAt": "2026-12-10T15:00:00+03:00",
            "endedAt": "2026-12-10T16:30:00+03:00",
        },
    },
    "train": {
        "summary": "Железнодорожный билет",
        "description": "Тот же контракт перемещения; kind меняется на train. Данные примера не являются расписанием.",
        "value": {
            "kind": "train",
            "departure": _point(
                "Вокзал отправления",
                "Москва, вокзал отправления (пример)",
                55.7734,
                37.6553,
            ),
            "arrival": _point(
                "Вокзал прибытия", "Казань, вокзал прибытия (пример)", 55.7887, 49.1004
            ),
            "startedAt": "2026-12-11T09:00:00+03:00",
            "endedAt": "2026-12-11T17:00:00+03:00",
        },
    },
    "ship": {
        "summary": "Билет на судно",
        "description": "Демонстрационное перемещение между двумя подтверждёнными причалами.",
        "value": {
            "kind": "ship",
            "departure": _point(
                "Причал отправления", "Казань, причал А (пример)", 55.7670, 49.1020
            ),
            "arrival": _point("Причал прибытия", "Причал Б (пример)", 55.7740, 48.6600),
            "startedAt": "2026-12-12T09:00:00+03:00",
            "endedAt": "2026-12-12T13:00:00+03:00",
        },
    },
    "stay": {
        "summary": "Гостиница: заселение и выселение",
        "description": "Нужна поездка с 10 по 13 декабря 2026 года включительно. Создаются одна точка и событие stay.",
        "value": {
            "kind": "stay",
            "stay": _point(
                "Отель (пример)", "Казань, ул. Примерная, 1", 55.7900, 49.1200
            ),
            "startedAt": "2026-12-10T15:00:00+03:00",
            "endedAt": "2026-12-13T12:00:00+03:00",
        },
    },
}

PREVIEW_EXAMPLES = {
    "ready": {
        "summary": "Авиабилет распознан, обязательные поля найдены",
        "value": {
            "kind": "plane",
            "departure": {
                "name": "SVO",
                "address": None,
                "iata": "SVO",
                "point": APPLY_EXAMPLES["plane"]["value"]["departure"],
            },
            "arrival": {
                "name": "KZN",
                "address": None,
                "iata": "KZN",
                "point": APPLY_EXAMPLES["plane"]["value"]["arrival"],
            },
            "stay": None,
            "startedAt": "2026-12-10T15:00:00+03:00",
            "endedAt": "2026-12-10T16:30:00+03:00",
            "startedAtHint": None,
            "endedAtHint": None,
            "issues": [],
            "readyToApply": True,
        },
    },
    "needs_confirmation": {
        "summary": "Гостиница: нужно подтвердить точку и часовой пояс",
        "description": "Это успешный разбор с HTTP 200. Поля из issues нужно исправить перед apply.",
        "value": {
            "kind": "stay",
            "departure": None,
            "arrival": None,
            "stay": {
                "name": "Отель (пример)",
                "address": "Казань, ул. Примерная, 1",
                "iata": None,
                "point": None,
            },
            "startedAt": None,
            "endedAt": None,
            "startedAtHint": {
                "month": 12,
                "day": 10,
                "localTime": "15:00:00",
                "localDate": "2026-12-10",
                "timeZone": None,
            },
            "endedAtHint": {
                "month": 12,
                "day": 13,
                "localTime": "12:00:00",
                "localDate": "2026-12-13",
                "timeZone": None,
            },
            "issues": [
                {
                    "field": "stay",
                    "code": "point_required",
                    "msg": "Confirm the stay point",
                },
                {
                    "field": "startedAt",
                    "code": "timezone_required",
                    "msg": "Confirm the start time zone",
                },
                {
                    "field": "endedAt",
                    "code": "timezone_required",
                    "msg": "Confirm the end time zone",
                },
            ],
            "readyToApply": False,
        },
    },
}


def _utc(value: str) -> str:
    return (
        datetime.fromisoformat(value).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )


def _apply_result(value: dict) -> dict:
    places = (
        [value["stay"]]
        if value["kind"] == "stay"
        else [value["departure"], value["arrival"]]
    )
    points = [
        {
            "id": str(2_000_000_000_000_000_001 + index),
            "name": point["name"],
            "description": point["address"],
            "latitude": point["latitude"],
            "longitude": point["longitude"],
            "visited": False,
        }
        for index, point in enumerate(places)
    ]
    times = {"startedAt": _utc(value["startedAt"]), "endedAt": _utc(value["endedAt"])}
    if value["kind"] == "stay":
        return {
            "points": points,
            "transfer": None,
            "event": {
                "id": "2000000000000000003",
                "title": value["stay"]["name"],
                "type": "stay",
                "description": None,
                "bookingUrl": None,
                "waypointId": points[0]["id"],
                **times,
            },
        }
    return {
        "points": points,
        "event": None,
        "transfer": {
            "id": "2000000000000000003",
            "transportType": value["kind"],
            "description": None,
            "startsAtWaypointId": points[0]["id"],
            "endsAtWaypointId": points[1]["id"],
            **times,
        },
    }


APPLY_RESPONSE_EXAMPLES = {
    key: {
        "summary": example["summary"],
        "description": "ID демонстрационные. Ответ содержит сохранённые записи, время приведено к UTC.",
        "value": _apply_result(example["value"]),
    }
    for key, example in APPLY_EXAMPLES.items()
}

PREVIEW_ERROR_EXAMPLES = {
    "source_required": {
        "summary": "Не выбран источник или одновременно переданы file и url",
        "value": {
            "detail": {},
            "occuredAt": "2026-12-10T12:00:00Z",
            "errorCode": "ValidationFailed",
            "errors": ["ticket_source_required"],
        },
    },
    "no_text": {
        "summary": "PDF не содержит извлекаемого текста",
        "value": {
            "detail": {},
            "occuredAt": "2026-12-10T12:00:00Z",
            "errorCode": "ValidationFailed",
            "errors": ["ticket_document_has_no_text"],
        },
    },
}

APPLY_ERROR_EXAMPLES = {
    "outside_trip": {
        "summary": "Подтверждённое время выходит за даты поездки",
        "value": {
            "detail": {},
            "occuredAt": "2026-12-10T12:00:00Z",
            "errorCode": "ValidationFailed",
            "errors": [
                {
                    "field": "startedAt",
                    "code": "outside_trip",
                    "msg": "startedAt is outside the trip dates in UTC",
                }
            ],
        },
    },
}
