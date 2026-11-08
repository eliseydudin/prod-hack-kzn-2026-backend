# Frontend: Google Drive, билеты, архивы, Web Push

Инструкция соответствует ветке `fix/geocoder` с исправлениями `f9bf268` и
`a30fb82`. OpenAPI развёрнутого API доступен по `/api/v1/openapi.json`, Swagger —
по `/api/v1/docs`. После обновления backend обновите сгенерированные типы frontend.

Демо-сайт: https://hack-kzn-05-vm1-40f58d.vm.prodcontest.com. Аккаунт TravelHub
для защиты — `drive_demo`; пароль передаёт владелец проекта отдельно. API-запросы
ниже выполняются относительно этого сайта.

## 1. Общий договор с frontend

- Во всех путях ниже уже указан префикс `/api/v1`.
- Защищённые запросы отправляются с `Authorization: Bearer <TravelHub access token>`.
  Это существующая авторизация приложения. Отдельный Google login здесь не нужен.
- Для работы cookies используйте `credentials: "include"` (`withCredentials: true`
  в Axios). Особенно это нужно запросу подключения Google Drive.
- ID в JSON — строки. Не преобразовывайте их в JavaScript `Number`.
- JSON-поля имеют camelCase; даты поездки — `YYYY-MM-DD`, время событий — ISO 8601
  с `Z` или смещением, например `2026-12-10T15:00:00+03:00`.
- Для `FormData` не выставляйте `Content-Type` вручную: браузер добавляет boundary.
- При `429` учитывайте `Retry-After`; при `401` используйте существующий сценарий
  обновления токена/входа. `404` может означать отсутствие доступа к поездке.

Ошибки backend обычно имеют поля `errorCode`, `detail`, `occuredAt`; ошибки
валидации дополнительно содержат `errors`. Ошибки Nginx могут иметь форму
`{ "error": { "code": "...", "message": "..." } }`, поэтому интерфейсу нужен
общий fallback по HTTP-статусу, если ожидаемых полей нет.

Примеры ниже используют переменную `accessToken` из существующей авторизации.
Значения ID, дат и координат в примерах демонстрационные.

Используйте относительные API-пути на том же HTTPS-домене, где открыт frontend.
Если нужен другой origin, согласуйте его с backend-разработчиком: CORS и cookies
должны разрешать этот сценарий. Возврат из Google ведёт на `/dashboard` того же origin.

## 2. Google Drive: подключение и фотографии

| Действие интерфейса | Запрос | Результат |
| --- | --- | --- |
| Получить состояние | `GET /api/v1/integrations/google-drive` | `enabled`, `status`, `email`, `revocationPending` |
| Подключить / переподключить | `POST /api/v1/integrations/google-drive/connect` | `authorizationUrl` и HttpOnly cookie |
| Сохранить фотографию | `POST /api/v1/integrations/google-drive/photo-exports` | `201`, объект экспорта |
| Проверить экспорт | `GET /api/v1/integrations/google-drive/photo-exports/{exportId}` | Сохранённое состояние экспорта |
| Отключить | `DELETE /api/v1/integrations/google-drive` | Новое состояние подключения |

### Подключение

По нажатию кнопки «Подключить Google Drive»:

```js
const response = await fetch("/api/v1/integrations/google-drive/connect", {
  method: "POST",
  credentials: "include",
  headers: { Authorization: `Bearer ${accessToken}` },
});
if (!response.ok) throw new Error(`Google Drive: HTTP ${response.status}`);
const { authorizationUrl } = await response.json();
window.location.assign(authorizationUrl);
```

Открывайте ссылку в том же браузере. Google вернёт браузер на backend callback,
который сам обменяет code и перенаправит на
`/dashboard?googleDrive=connected` либо `?googleDrive=denied`.
Frontend не вызывает callback вручную и не обменивает Google code на токены.

На странице `/dashboard` покажите результат, уберите обработанный query-параметр
из адреса и заново выполните GET состояния. При ошибке callback backend может
вернуть HTTP-ошибку вместо перенаправления.

Состояния: `disconnected`, `connected`, `reconnect_required`, `disabled`.
При `enabled=false` скрывайте или выключайте действия Drive. При
`reconnect_required` предлагайте повторное подключение.

### Загрузка

JPEG/PNG, до 10 000 000 байт по умолчанию и 25 млн пикселей. Кнопка отправляет
выбранный пользователем `File`:

```js
const operationKey = crypto.randomUUID();
const body = new FormData();
body.append("file", selectedFile);
const response = await fetch("/api/v1/integrations/google-drive/photo-exports", {
  method: "POST",
  credentials: "include",
  headers: {
    Authorization: `Bearer ${accessToken}`,
    "Idempotency-Key": operationKey,
  },
  body,
});
if (!response.ok) throw new Error(`Экспорт фото: HTTP ${response.status}`);
const exported = await response.json();
```

Сохраните `operationKey`, исходный файл и `exported.id` в состоянии операции.
При `succeeded` покажите `name` и ссылку `webViewLink`. Фото сохраняется в папку
`TravelHub` в личном Drive; ссылка не делает его общедоступным.

При сетевом сбое или `uncertain` повторяйте POST с тем же ключом, именем и байтами.
GET состояния сам загрузку не повторяет; фонового worker для этих экспортов нет.
Если ответ ошибки содержит `detail.exportId`, сохраните его для проверки состояния.
Новая пользовательская загрузка получает новый ключ. Не пытайтесь автоматически
повторять все `409`: это также может быть другой файл с тем же ключом, смена
подключения или необходимость нового consent.

Для интерфейса полезно отдельно обработать `413` (размер), `415` (формат),
`409` (конфликт/подключение) и `503` (временно недоступно или выключено).

После DELETE перечитайте состояние. При `revocationPending=true` предложите
повтор отключения: новое подключение пока блокируется. Экспортированные фото
остаются в Drive. Если пользователь удаляет аккаунт TravelHub, отзовите Drive
до удаления аккаунта, пока его авторизация ещё действует.

## 3. Импорт билета и внешний геокодер

Сценарий: выбрать документ → получить preview → проверить/исправить поля →
нажать «Добавить в поездку». Preview ничего не сохраняет.

| Этап | Запрос | Тело |
| --- | --- | --- |
| Распознать | `POST /api/v1/trips/{tripId}/tickets/preview` | `FormData`: ровно одно из `file` или `url`, необязательно `kind` |
| Подтвердить | `POST /api/v1/trips/{tripId}/tickets/apply` | JSON с подтверждёнными точками и временем |

`file` — PDF с текстовым слоем до 900 KiB и 8 страниц. `url` — публичная HTTPS-ссылка
на поддерживаемый PDF/HTML; страницы с авторизацией и JS-приложения могут не
распознаваться. OCR сканов не реализован. `kind`: `plane`, `train`, `ship`, `stay`.

Preview содержит `kind`, `departure`, `arrival` или `stay`, времена `startedAt`,
`endedAt`, подсказки `startedAtHint`/`endedAtHint`, массив `issues` и `readyToApply`.
У места есть `name`, `address`, `iata`, `point`; `point` может быть `null`.

Геокодер уже вызывается backend внутри preview. Отдельного frontend-запроса к
Photon в этом сценарии не нужно. Если результат неоднозначен или геокодер
недоступен, покажите выбор точки на карте и заполните координаты вручную.

Показывайте проблемы из `issues` рядом с соответствующим полем. Коды:
`point_required`, `start_required`, `date_required`, `timezone_required`,
`outside_trip`, `ended_before_start`. Подсказки содержат `localDate`, `localTime`,
`timeZone`, `month`, `day`: используйте их для заполнения формы. `datetime-local`
не содержит часовой пояс — перед apply сформируйте момент с явным смещением.

Пример JSON для apply после подтверждения:

```json
{
  "kind": "plane",
  "departure": {
    "name": "Аэропорт SVO",
    "address": "Аэропорт Шереметьево, Москва",
    "latitude": 55.9726,
    "longitude": 37.4146
  },
  "arrival": {
    "name": "Аэропорт KZN",
    "address": "Аэропорт Казань",
    "latitude": 55.6062,
    "longitude": 49.2787
  },
  "startedAt": "2026-12-10T15:00:00+03:00",
  "endedAt": "2026-12-10T16:30:00+03:00"
}
```

В apply передаются `preview.departure.point` и `preview.arrival.point`, а не
объекты мест целиком. Для `stay` вместо departure/arrival передайте одно поле
`stay` с теми же четырьмя полями точки. `startedAt` обязателен, `endedAt` можно
опустить. Сервер проверяет попадание дат в поездку по UTC.

Ответ `201`: `points`, `transfer` и/или `event`. После него обновите списки
точек, перемещений и событий поездки. Повтор одинакового подтверждения использует
существующие записи. Даже при `readyToApply=true` сначала показывайте результат
пользователю. Примеры всех четырёх типов есть в Swagger и
[примере с PDF](examples/tickets/README.md).

## 4. JSON-экспорт и импорт поездки

| Кнопка | Запрос | Действие frontend |
| --- | --- | --- |
| Скачать JSON | `GET /api/v1/trips/{tripId}/export` | Скачать ответ как Blob в `trip-{tripId}.json` |
| Импортировать JSON | `POST /api/v1/trips/import` | Отправить содержимое выбранного файла как `application/json` |

Для скачивания используйте авторизованный fetch, затем `response.blob()`,
`URL.createObjectURL()` и ссылку с атрибутом `download`. После скачивания освободите
Object URL. Прямая ссылка без Bearer не заменяет авторизованный запрос.

Импорт принимает JSON целиком, не multipart. Лимит архива — 1 MiB; структура:
`version: 1`, `trip`, `points`, `events`, `transfers`. Проще отправлять ранее
скачанный архив без преобразований; подробная схема находится в OpenAPI.

Ответ импорта `201` содержит новую поездку: откройте её по возвращённому `id` и
обновите список поездок. Импорт создаёт копию с новыми ID и текущим пользователем
как владельцем; он не обновляет исходную поездку. Участники, приглашения и файл
обложки в архив не входят. Не повторяйте POST импорта автоматически при тайм-ауте:
повтор может создать вторую копию. Обработайте `413`, `422`, а при экспорте также
`409`, если данные поездки не позволяют собрать архив.

## 5. Web Push

### Подключение браузера

| Действие | Запрос | Результат |
| --- | --- | --- |
| Получить публичный VAPID-ключ | `GET /api/v1/push/vapid-public-key` | `{ "publicKey": "..." }`, Bearer не обязателен |
| Сохранить браузерную подписку | `POST /api/v1/push/subscriptions` | `200`, `{ "id": "..." }` |
| Тестовая отправка | `POST /api/v1/push/test` | Тело `{ "subscriptionId": "..." }`, ответ `202` |
| Удалить подписку | `DELETE /api/v1/push/subscriptions/{subscriptionId}` | `204` |

1. На HTTPS-странице по нажатию «Включить уведомления» запросите разрешение через
   `Notification.requestPermission()`. Обработайте `denied` и отсутствие поддержки.
2. Зарегистрируйте frontend Service Worker, например `/sw.js`, дождитесь его
   готовности. Это статический файл frontend, backend-маршрут для него не нужен.
3. Получите `publicKey`, преобразуйте base64url в `Uint8Array` и вызовите
   `registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })`.
   Если подписка уже существует, используйте `getSubscription()`.
4. Из `subscription.toJSON()` отправьте `endpoint` и `keys`:

```json
{
  "endpoint": "https://fcm.googleapis.com/example-browser-generated-endpoint",
  "keys": {
    "p256dh": "<реальный ключ из subscription.toJSON()>",
    "auth": "<реальный ключ из subscription.toJSON()>"
  }
}
```

Это форма запроса, а не рабочие значения: endpoint и ключи берите из браузерной
подписки. Сохраните возвращённый `id` вместе с текущим аккаунтом и подпиской.

Service Worker должен обрабатывать событие `push` и вызывать
`self.registration.showNotification(title, { body })`; backend присылает JSON с
`title` и `body`. В `notificationclick` можно открывать страницу приложения.

`202` от `/push/test` означает принятие задачи в очередь. Фактическую доставку
проверьте по появлению уведомления. При отсутствии доставки передайте ошибку backend-разработчику.
Это обновление содержит тестовую отправку;
автоматические напоминания о поездках к ней не подключены.

При выключении уведомлений удалите серверную подписку и вызовите
`subscription.unsubscribe()` в браузере. Делайте это до выхода/смены аккаунта,
пока доступен токен прежнего пользователя: endpoint нельзя переназначить другому
аккаунту, не удалив прежнюю регистрацию. Если пришёл `409`, проверьте эту привязку;
при `503` — настройки ключа и очереди.

## 6. Существующие экраны, затронутые обновлением

- Список `/api/v1/trips/` включает свои и совместные поездки. Участники могут
  редактировать содержимое; удаление всей поездки доступно владельцу.
- Состав участников: `GET /api/v1/trips/{tripId}/collaboratrs`.
  В существующем URL именно `collaboratrs`; ответ содержит `owner` и `collaborators`.
- Создание приглашения: `POST /api/v1/trips/{tripId}/code/generate?max_uses=10`.
  Ответ содержит `code`, `uses`; URL приглашения формирует frontend.
  Присоединение: `POST /api/v1/trips/code/redeem/{code}`, затем обновите список поездок.
- Для PATCH опущенное поле сохраняет значение, `null` очищает допускающее null
  поле. Например, описание очищается через `{ "description": null }`, а не `""`.
- Загрузка обложки: `PUT /api/v1/trips/{tripId}/cover`, тело — сам `File`,
  `Content-Type` — JPEG/PNG/WebP, до 5 MiB. Это другой формат, чем multipart Drive.
  Возвращённый `cover` используйте как URL. Защищённую обложку получайте с Bearer
  через fetch → Blob → Object URL; обычный `<img src>` Bearer не добавляет.
- Поле точки `visited` меняется существующим PATCH точки. Событие может быть
  связано с точкой через `waypointId`. Актуальные DTO смотрите в OpenAPI.

## 7. Приёмка после подключения frontend

1. На production-домене получить состояние Drive, подключить Google и вернуться
   на существующий `/dashboard` без 404; GET должен показать `connected`.
2. Загрузить PNG и повторить исходный запрос с тем же ключом: должна остаться
   одна фотография. Проверить отключение и повторное подключение.
3. Загрузить тестовый PDF, исправить отсутствующие координаты/часовой пояс,
   подтвердить и увидеть точки и перемещение в поездке. Повтор не создаёт дубли.
4. Скачать JSON поездки и импортировать его: должна появиться отдельная поездка
   с точками, событиями и перемещениями.
5. Включить Push по пользовательской кнопке, выполнить тестовую отправку и увидеть
   уведомление. Проверить отключение и смену аккаунта.

Google Drive доступен тестовому Google-аккаунту, настроенному для защиты проекта.
Для `drive_demo` уже подключён `vakudoo@gmail.com`: загрузка PNG, повтор с тем же
ключом без дубля и чтение состояния экспорта проверены через production API.
Google OAuth работает в режиме Testing: refresh token истекает через 7 дней.
При `reconnect_required` покажите повторное подключение через `/connect`.
