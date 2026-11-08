## Фото и документы в RustFS

Создайте UUID один раз для каждого файла (`crypto.randomUUID()` в браузере).
Отправьте `PUT /api/v1/files/{file_id}` с `Authorization: Bearer <token>` и
передайте `File` или `Blob` напрямую в теле запроса. Укажите его `Content-Type`.
API принимает JPEG, PNG, WebP и PDF до 10 МиБ и возвращает `204` при успехе.
Повторный `PUT` с тем же UUID заменяет файл. Сохраните UUID вместе с метаданными файла.

`GET /api/v1/files/{file_id}` с токеном того же аккаунта возвращает байты файла.
Для чужого аккаунта API возвращает `404`. PDF отдаётся как вложение. Если фронтенд
использует прокси `/api/backend`, добавьте путь `files/{file_id}` в список
разрешённых маршрутов прокси.

## Web Push

Docker Compose запускает Celery-воркер вместе с API. Он получает задачи через Valkey по `REDIS_URL`. `POST /api/v1/push/test` возвращает `202 Accepted`, когда задача принята в очередь. Ошибки доставки видны в логах `worker`.

Для первого запуска создайте ключ VAPID в общем томе API и воркера. Команда сохраняет уже существующий ключ.

```sh
docker compose run --rm --no-deps --user root api sh -ec '
  cd /var/lib/app/vapid
  if [ ! -e private_key.pem ]; then
    umask 077
    vapid --gen
  fi
  chown 1001:1001 private_key.pem
  chmod 600 private_key.pem
'
```

На сервере выполните команду из каталога развёртывания API с его Compose-конфигурацией. Сохраняйте том `vapid`, чтобы действующие браузерные подписки продолжали работать.

## Пользовательские метрики в Sage

API с настроенным `REDIS_URL` раз в минуту публикует агрегаты. Vector добавляет их к существующим метрикам Prometheus, а Sage забирает их через действующий защищённый `/sage-metrics`. В Valkey хранится последнее время активности каждого аккаунта. Сборщик удаляет записи старше 7 дней. В метрики Sage попадают только числа.

| Метрика | Что считает |
| --- | --- |
| `travelhub_product_active_users_24h`, `travelhub_product_active_users_7d` | Уникальные обычные аккаунты с успешным запросом к поездкам, точкам, перемещениям или событиям за последние 24 часа или 7 дней. |
| `travelhub_product_new_accounts_24h` | Обычные аккаунты, созданные за последние 24 часа, включая аккаунты, созданные администратором. |
| `travelhub_product_enabled_accounts` | Текущее число включённых обычных аккаунтов. |
| `travelhub_product_trip_owners` | Включённые обычные аккаунты, которым принадлежит хотя бы одна поездка. |
| `travelhub_product_trip_owners_with_mapped_events` | Владельцы, у которых хотя бы в одной поездке есть событие с временем и привязанной точкой. Учитываются события, добавленные любым участником поездки. |

После выкладки API обновите `docker/monitoring/vector.toml` на ВМ и пересоздайте сервис `vector` в действующем стеке мониторинга (`docker compose -f docker-compose.monitoring.yml up -d --force-recreate vector` из его каталога). После очередного опроса проверьте данные в [Sage Search](https://sage.spirit.t-education.com/search). Для панели активности за 24 часа используйте:

```promql
pql travelhub_product_active_users_24h{group="hack-kzn-05", env="prod", system="travelhub"}
and (time() - travelhub_product_sample_timestamp_seconds{group="hack-kzn-05", env="prod", system="travelhub"} < 180)
```

Для доли активации используйте запрос ниже и формат **Percent (0.0–1.0)** в Grafana. При отсутствии владельцев поездок или свежего замера панель останется пустой.

```promql
pql (
  travelhub_product_trip_owners_with_mapped_events{group="hack-kzn-05", env="prod", system="travelhub"}
  / (travelhub_product_trip_owners{group="hack-kzn-05", env="prod", system="travelhub"} > 0)
)
and (time() - travelhub_product_sample_timestamp_seconds{group="hack-kzn-05", env="prod", system="travelhub"} < 180)
```

Активность накапливается с момента выкладки. Доля активации отражает текущее состояние поездок и может уменьшиться после удаления событий или поездок.
