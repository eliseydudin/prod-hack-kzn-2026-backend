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
