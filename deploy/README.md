# Деплой Modish (Alpha)

## P1 — HTTPS

1. DNS: `api.YOUR_DOMAIN.com` → IP VPS.
2. На сервере: `sudo apt install nginx certbot python3-certbot-nginx`
3. Скопировать `deploy/nginx/modish-api.conf`, заменить `YOUR_DOMAIN`.
4. `sudo certbot --nginx -d api.YOUR_DOMAIN.com`
5. Backend: `docker compose -f deploy/docker-compose.prod.yml up -d --build`

## `.env` на сервере

Скопируйте `backend/.env.example` → `backend/.env` и задайте:

```env
DATABASE_URL=postgresql://modish:YOUR_PASSWORD@db:5432/modish
JWT_SECRET=длинная-случайная-строка
POSTGRES_PASSWORD=YOUR_PASSWORD
```

Важно: в Docker hostname БД — **`db`**, не `localhost`.

Миграции применяются автоматически при старте контейнера (`backend/scripts/entrypoint.sh`).

Проверка:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","database":"ok","version":"1.0.0-alpha"}
```

## Flutter (release)

По умолчанию release APK использует `lib/core/config/api_config.dart` (`productionApiBaseUrl`).

Или явно при сборке:

```bash
flutter build apk --release \
  --dart-define=MODISH_API_BASE_URL=http://194.87.118.236
```

После HTTPS:

```bash
flutter build apk --release \
  --dart-define=MODISH_API_BASE_URL=https://api.YOUR_DOMAIN.com
```

## Обновление на сервере

```bash
cd /opt/modish
git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

## Секреты

- Не коммитить `backend/.env`
- `JWT_SECRET`, `DATABASE_URL`, Admitad-токены только в `.env` на сервере

## Бэкап PostgreSQL (cron)

```bash
0 3 * * * pg_dump "$DATABASE_URL" | gzip > /var/backups/modish-$(date +\%F).sql.gz
```

## Если «сервер недоступен» в приложении

1. `curl http://194.87.118.236/health` с ПК — должен ответить JSON.
2. На VPS: `docker compose -f deploy/docker-compose.prod.yml ps` и `docker compose logs api --tail 50`.
3. Убедитесь, что порт 80/443 открыт в firewall и nginx проксирует на `127.0.0.1:8000`.
4. Пересоберите APK после смены `productionApiBaseUrl`.
