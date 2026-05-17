# Деплой Modish (Alpha)

## P1 — HTTPS

1. DNS: `api.YOUR_DOMAIN.com` → IP VPS.
2. На сервере: `sudo apt install nginx certbot python3-certbot-nginx`
3. Скопировать `deploy/nginx/modish-api.conf`, заменить `YOUR_DOMAIN`.
4. `sudo certbot --nginx -d api.YOUR_DOMAIN.com`
5. Backend: `docker compose -f deploy/docker-compose.prod.yml up -d --build`

## Flutter (release)

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
