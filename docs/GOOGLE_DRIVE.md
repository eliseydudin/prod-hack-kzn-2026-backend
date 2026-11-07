# Google Drive: подключение и экспорт фото

Модуль сохраняет выбранный пользователем JPEG/PNG на его личный Google Drive в папку `TravelHub`. Источник первого этапа — multipart-файл из браузера. Модуль не скачивает URL и не зависит от хранилища обложек поездок. Импорт и PDF в эту версию не входят.

## Настройка

По умолчанию интеграция выключена. Для включения нужны Google Cloud project с Drive API, OAuth client типа **Web application**, consent screen со scope `https://www.googleapis.com/auth/drive.file` и зарегистрированный точный callback URL.

При запуске API напрямую добавьте переменные из `.env.example` в его окружение. В Docker Compose запишите их в `.env.google-drive` рядом с compose-файлом: локальный и prod-сервисы API подключают этот необязательный файл. Он исключён из Git и Docker build context.

```dotenv
GOOGLE_DRIVE_ENABLED=true
GOOGLE_DRIVE_CLIENT_ID=<web-client-id>
GOOGLE_DRIVE_CLIENT_SECRET=<client-secret>
GOOGLE_DRIVE_REDIRECT_URI=https://your-domain/api/v1/integrations/google-drive/callback
GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY=<fernet-key>
GOOGLE_DRIVE_RETURN_PATH=/dashboard
GOOGLE_DRIVE_MAX_PHOTO_BYTES=10000000
```

Fernet-ключ генерируется отдельно от JWT и client secret:

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Ключ хранится в секретах окружения, не в Git. Смена или потеря ключа делает старые подключения нечитаемыми: перенос окружения требует сохранить тот же ключ. Автоматическая ротация ключей в этой версии не реализована.

Примените `alembic upgrade head`. Merge-миграция `0012_google_drive_merge` соединяет опубликованную ревизию `0006_google_drive` с ревизией `0011` для Web Push. Обновление доступно с обеих ревизий. На сервере `.env.google-drive` создаётся отдельно с доступом только владельцу. Настройки RustFS хранятся в `.env.storage`.

В Google OAuth Testing добавьте тестовых пользователей; refresh tokens со scope Drive в этом режиме действуют 7 дней. [Google: срок жизни токенов](https://developers.google.com/identity/protocols/oauth2#expiration).

## API

Общий внешний префикс — `/api/v1/integrations/google-drive`. Все маршруты, кроме callback, требуют обычный Bearer TravelHub.

| Метод и путь | Результат |
|---|---|
| `POST /connect` | `200`, `authorizationUrl`, защищённая cookie привязки браузера на 10 минут |
| `GET /callback?code=...&state=...` | Однократная обработка согласия, `303` на локальный `GOOGLE_DRIVE_RETURN_PATH?googleDrive=connected` или `denied` |
| `GET` | Свой `enabled`, `status`, `email`, `revocationPending`; токенов нет |
| `DELETE` | `200`, отключённое состояние и результат отзыва доступа |
| `POST /photo-exports` | Multipart-поле `file`, заголовок `Idempotency-Key`; `201`, объект экспорта |
| `GET /photo-exports/{exportId}` | Свой экспорт; чужой или неизвестный ID даёт `404` |

`POST /connect` вызывается с `credentials: 'include'`, затем браузер переходит на возвращённый `authorizationUrl`. Cookie — HttpOnly, SameSite=Lax, Secure для HTTPS; при локальной разработке разрешён HTTP callback на localhost. Callback связывает Google-аккаунт с пользователем, который начал подключение, через случайный state и отдельную cookie. Хеши state и cookie хранятся в PostgreSQL; повтор, истечение срока или несовпадение браузера не подключают аккаунт.

Фронт должен показать результат подключения и перечитать `GET` после возврата на dashboard. Кнопки/обработка параметра `googleDrive` в существующем фронте этим backend-коммитом не добавляются.

При выключенной интеграции `GET` возвращает `enabled=false`, а подключение, callback, экспорт и отзыв возвращают `503`. История ранее созданных экспортов остаётся доступна владельцу через `GET /photo-exports/{exportId}`.

Для экспорта создайте `FormData`, добавьте поле `file` и отправьте его с Bearer и новым ключом операции. Заголовок Content-Type для FormData выставляет браузер. Фото проверяется по фактическому формату, декодируется вне event loop; лимит — 10 000 000 байт по умолчанию и 25 млн пикселей. Имя очищается, расширение соответствует реальному формату. Общий multipart body ограничен размером фото плюс 64 KiB, включая запросы без Content-Length.

Пример ответа:

```json
{
  "id": "1568564968689212259",
  "name": "photo.png",
  "mimeType": "image/png",
  "size": 125000,
  "status": "succeeded",
  "driveFileId": "google-file-id",
  "webViewLink": "https://drive.google.com/file/d/google-file-id/view",
  "errorReason": null,
  "createdAt": "2026-11-07T12:00:00Z"
}
```

Это пример формы ответа, а не настоящий Google file ID. Открытие ссылки требует прав на файл в Drive: публичный доступ модуль не создаёт.

## Повторы, ошибки и отключение

Одна попытка экспорта обрабатывается в запросе. Устойчивого фонового worker и хранения исходного файла здесь нет. При перезапуске или тайм-ауте повторите POST с **тем же ключом, именем и байтами**. В БД до записи в Google сохраняются ID файла/папки и отпечаток источника. Повтор использует эти ID; уже существующий файл проверяется по размеру, MD5, MIME, папке и метке операции. Ответ `409` Google сам по себе не считается успешной загрузкой. [Google: заранее выделенные ID](https://developers.google.com/workspace/drive/api/guides/manage-uploads#pre-generated-ids).

Ключ имеет длину 1–128 символов, разрешены латинские буквы, цифры, `.`, `_`, `:`, `-`. Другой файл с тем же ключом или повтор от старого подключения дают `409`. Для повторов завершённой операции также возвращается `201` и прежний ID. Состояния операции: `pending`, `succeeded`, `uncertain`, `failed`. `GET` читает сохранённое состояние; согласование неопределённого результата выполняет повтор POST с исходным фото.

Обычные ошибки имеют существующий формат backend (`detail`, `occuredAt`, `errorCode`):

- `400`: повреждённое изображение/неверная OAuth-сессия;
- `401`: нет действующей авторизации TravelHub;
- `409`: подключение отсутствует/изменилось, нужен новый Google consent, операция уже выполняется, ключ не соответствует файлу или недоступна папка;
- `413`: превышен лимит фото или multipart body;
- `415`: фактический формат отличается от JPEG/PNG;
- `429`: общая с поездками пользовательская квота Valkey; время повтора — в `Retry-After`. Callback этой авторизованной квотой не ограничивается;
- `503`: интеграция выключена или Google недоступен. Если операция уже создана, в `detail.exportId` будет её ID.

Временная ошибка после отправки фото сохраняет `uncertain`; используйте прежний ключ при повторе. При `google_reconnect_required` статус подключения становится `reconnect_required`. Токены Google не заменяют Bearer TravelHub и не входят в публичные DTO или сообщения ошибок. HTTP-запросы к Google имеют тайм-ауты, перенаправления не следуются, загрузки не повторяются автоматически.

Refresh token зашифрован Fernet; access token существует только во время операции. Изменение подключения, refresh и экспорт одного владельца сериализованы блокировкой PostgreSQL между процессами. После сохранения зарезервированного ID версия подключения проверяется повторно до загрузки.

Повторный consent без нового refresh token может использовать сохранённый токен только для того же Google-аккаунта и действующего подключения. Если прежний токен уже потребовал переподключения, нужен новый offline consent. Смена Google-аккаунта создаёт новую папку и поколение подключения; старые ключи экспорта дают `409`. Сохранённые ссылки на старые файлы остаются в истории.

Отключение сразу запрещает новые операции. При сбое Google revoke возвращается `revocationPending=true`; повторите DELETE, пока отзыв не завершится. Новое подключение блокируется до завершения ожидающего отзыва. Экспортированные файлы не удаляются. Если папка TravelHub была удалена/помещена в корзину, отключите и подключите Drive заново: будет создана новая папка.

Для Drive используйте отдельный Google Cloud project от независимого Google login: [Google отзывает разрешения пользователя для проекта](https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke). Nginx и фильтр Uvicorn access log исключают query string callback с code/state. Ответы модуля имеют `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.

Текущий backend удаляет аккаунт TravelHub мягко (`enabled=false`): старый Bearer и незавершённый Drive callback теряют доступ, но Google grant и зашифрованный refresh token сохраняются. Для отзыва сначала вызовите `DELETE /integrations/google-drive`, затем удаляйте аккаунт TravelHub; после удаления отозвать grant можно в настройках Google. Физическое удаление строки пользователя с Drive-данными запрещено внешними ключами. Переключение на другой Google-аккаунт само по себе не отзывает grant прежнего аккаунта. Если один Google-аккаунт использован для нескольких пользователей TravelHub, Google revoke затрагивает его разрешения всему Cloud project.

Для пути экспорта Nginx допускает multipart body до 20 MiB и ждёт backend до 180 секунд. Внутри API остаются отдельные лимиты фактического фото и тела запроса. Остальные маршруты сохраняют прежний лимит Nginx. При слишком большом теле прокси возвращает JSON `413`.

## Проверка

```sh
uv sync --locked
alembic upgrade head
pytest -c test/pytest.ini test/unit/test_google_drive_provider.py test/integration/test_google_drive.py
alembic check
ruff check app test migrations/versions/0006_google_drive.py migrations/versions/0012_google_drive_merge.py
```

Интеграционные тесты используют настроенную тестовую PostgreSQL и подменяют внешний Google: проверяются OAuth binding/replay/expiry, смена аккаунта, владение, идемпотентность после неизвестного результата/новой DB-сессии, fingerprint, межпроцессная блокировка, отзыв и лимит multipart body. Настоящий consent и появление фото в Google Drive нужно отдельно проверить после настройки Google Cloud и frontend.
