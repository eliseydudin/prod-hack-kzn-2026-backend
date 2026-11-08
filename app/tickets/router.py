from asyncio import gather
from typing import Annotated

from fastapi import APIRouter, Body, File, Form, Path, Response, UploadFile, status

from app.auth.tokens import CurrentUser
from app.database.connection import SessionDep
from app.models.ticket import (
    TicketApplyRequest,
    TicketApplyResult,
    TicketIssue,
    TicketKind,
    TicketPlaceMatch,
    TicketPreview,
)
from app.models.types.snowflake_id import SnowflakeId
from app.responses import AuthorizationError, NotFoundError, ValidationError, responses
from app.tickets.crud import apply_ticket
from app.tickets.examples import (
    APPLY_ERROR_EXAMPLES,
    APPLY_EXAMPLES,
    APPLY_RESPONSE_EXAMPLES,
    PREVIEW_ERROR_EXAMPLES,
    PREVIEW_EXAMPLES,
)
from app.tickets.geocoding import resolve_ticket_place
from app.tickets.parser import TicketParseError, parse_ticket
from app.tickets.source import TicketSourceError, read_ticket_text
from app.tickets.timing import prepare_ticket_preview
from app.trips.crud import get_owned_trip

router = APIRouter(prefix="/trips/{tripId}/tickets", tags=["tickets"])


@router.post(
    "/preview",
    summary="1. Разобрать билет или бронирование без сохранения",
    description="""
Авторизуйтесь через **Authorize** и укажите `tripId` существующей доступной вам поездки.

**Выберите один источник:** PDF в `file` **или** публичную HTTPS-ссылку в `url`.
При загрузке PDF оставьте `url` пустым; не отправляйте пустые поля через «Send empty value».
Файл должен содержать текстовый слой: скан-картинка без OCR не подходит.
Лимиты: **900 КиБ**, **8 страниц**. Ссылка может вести на PDF или HTML-страницу;
`localhost`, приватные адреса и страницы, требующие входа, не подходят.

Поддерживаются авиабилеты (`plane`), железнодорожные (`train`), билеты на судно (`ship`)
и гостиницы (`stay`), RU/EN. `kind` можно не передавать для автоматического определения.

**Preview ничего не сохраняет.** В ответе будут найденные места, координаты, время,
подсказки `startedAtHint` / `endedAtHint`, список `issues` и `readyToApply`.
HTTP 200 с `readyToApply: false` означает, что нужно уточнить поля из `issues`.
Неизвестные координаты и часовой пояс сервер не выдумывает.

Затем подтвердите данные отдельным **POST /tickets/apply** ниже.
Для демонстрационных примеров сначала создайте поездку с датами
`startedOn: 2026-12-10`, `endedOn: 2026-12-13`.
Готовый тестовый PDF находится в репозитории: `docs/examples/tickets/flight.pdf`.
""",
    response_description="Распознанные поля и список уточнений; записей в поездке ещё нет.",
    responses={
        **responses(AuthorizationError, NotFoundError),
        200: {"content": {"application/json": {"examples": PREVIEW_EXAMPLES}}},
        422: {
            **responses(ValidationError)[422],
            "description": "Неверный источник или нечитаемый документ.",
            "content": {"application/json": {"examples": PREVIEW_ERROR_EXAMPLES}},
        },
    },
)
async def post_ticket_preview(
    trip_id: Annotated[
        SnowflakeId,
        Path(
            alias="tripId",
            gt=0,
            description="Реальный строковый ID поездки из POST /trips/ или GET /trips/. Примеры ниже рассчитаны на 10–13 декабря 2026 года.",
        ),
    ],
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[
        UploadFile | None,
        File(
            description="PDF с текстовым слоем, до 900 КиБ и 8 страниц. Передайте либо file, либо url."
        ),
    ] = None,
    url: Annotated[
        str | None,
        Form(
            description="Публичный HTTPS URL PDF/HTML без авторизации. При выборе file не отправляйте это поле."
        ),
    ] = None,
    kind: Annotated[
        TicketKind | None,
        Form(
            description="Необязательная подсказка: plane / train / ship / stay. Не передавайте поле для автоопределения."
        ),
    ] = None,
) -> TicketPreview:
    trip = await get_owned_trip(session, user, trip_id)
    trip_start, trip_end = trip.started_on, trip.ended_on
    await session.rollback()
    try:
        parsed = parse_ticket(await read_ticket_text(file, url), kind)
    except (TicketSourceError, TicketParseError) as error:
        raise ValidationError(errors=[str(error)]) from error

    points = await gather(
        *(resolve_ticket_place(place, parsed.kind) for place in parsed.places)
    )
    places = [
        TicketPlaceMatch.model_validate(place, update={"point": point})
        for place, point in zip(parsed.places, points, strict=True)
    ]
    preview = prepare_ticket_preview(parsed, trip_start, trip_end)
    if parsed.kind == TicketKind.STAY:
        preview.stay = places[0]
        fields = ("stay",)
    else:
        preview.departure, preview.arrival = places
        fields = ("departure", "arrival")
    preview.issues.extend(
        TicketIssue(
            field=field, code="point_required", msg=f"Confirm the {field} point"
        )
        for field, place in zip(fields, places, strict=True)
        if place.point is None
    )
    response.headers["Cache-Control"] = "no-store"
    return preview


@router.post(
    "/apply",
    status_code=status.HTTP_201_CREATED,
    summary="2. Сохранить подтверждённый билет или гостиницу",
    description="""
Выберите готовый запрос в выпадающем списке **Examples**: авиабилет, поезд, судно или гостиница.
Укажите реальный `tripId`. Примеры рассчитаны на поездку **10–13 декабря 2026 года**;
для своей поездки измените даты, точки и координаты.

Для `plane` / `train` / `ship` передайте **departure** и **arrival** — подтверждённые точки.
Из ответа preview это `departure.point` и `arrival.point`, без внешней оболочки места.
Для `stay` передайте одну точку **stay** (из `preview.stay.point`).
У каждой точки нужны `name`, `address`, `latitude` и `longitude`.
Целиком объект preview в этот запрос передавать не нужно.

`startedAt` обязателен, `endedAt` можно пропустить или передать `null`.
Время должно включать UTC-смещение: `2026-12-10T15:00:00+03:00` или `2026-12-10T12:00:00Z`.
Сервер сохраняет время в UTC и проверяет попадание в дни поездки **по UTC**.

Для билета создаются точки и перемещение, для гостиницы — точка и событие `stay`.
Операция атомарна. Повтор одинакового подтверждения в той же поездке возвращает
те же записи без дублей, также с HTTP **201**. ID в примерах ответа демонстрационные.
""",
    response_description="Сохранённые точки и перемещение либо событие stay. Время возвращается в UTC.",
    responses={
        **responses(AuthorizationError, NotFoundError),
        201: {"content": {"application/json": {"examples": APPLY_RESPONSE_EXAMPLES}}},
        422: {
            **responses(ValidationError)[422],
            "description": "Неполное подтверждение, неверные координаты или время/дни поездки.",
            "content": {"application/json": {"examples": APPLY_ERROR_EXAMPLES}},
        },
    },
)
async def post_ticket_apply(
    trip_id: Annotated[
        SnowflakeId,
        Path(
            alias="tripId",
            gt=0,
            description="ID существующей поездки. Замените даты примера, если у вашей поездки другой диапазон.",
        ),
    ],
    request: Annotated[
        TicketApplyRequest, Body(discriminator="kind", openapi_examples=APPLY_EXAMPLES)
    ],
    session: SessionDep,
    user: CurrentUser,
) -> TicketApplyResult:
    return await apply_ticket(session, user, trip_id, request)
