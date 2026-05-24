# Деплой Modish (Alpha)

## Первый запуск на VPS (с нуля)

На сервере **нет** `/opt/modish` — его нужно создать и загрузить проект.

### Шаг 1 — загрузить файлы с ПК (PowerShell)

```powershell
scp -r C:\Users\User\Desktop\modish\backend root@194.87.118.236:/opt/modish/
scp -r C:\Users\User\Desktop\modish\deploy root@194.87.118.236:/opt/modish/
scp C:\Users\User\Desktop\modish\deploy\setup-vps.sh root@194.87.118.236:/root/
```

### Шаг 2 — на сервере (SSH)

```bash
bash /root/setup-vps.sh
```

Скрипт установит Docker, создаст `.env`, поднимет PostgreSQL + API на **порту 80**.

### Шаг 3 — проверка

```bash
curl http://127.0.0.1/health
curl http://194.87.118.236/health
```

Должно вернуть: `{"status":"ok","database":"ok",...}`

### Шаг 4 — `backend/.env` (обязательно для Docker)

Файл `backend/.env` на сервере. Минимум:

```env
POSTGRES_PASSWORD=придумайте_длинный_пароль
DATABASE_URL=postgresql+psycopg://modish:придумайте_длинный_пароль@db:5432/modish
JWT_SECRET=длинная-случайная-строка
ADMIN_CATALOG_TOKEN=другая-случайная-строка
```

Важно: в `DATABASE_URL` хост **`db`**, не `localhost`. Пароль в `POSTGRES_PASSWORD` и в URL — **один и тот же**.

Если в пароле есть символ `$`, в `.env` для Docker пишите `$$` вместо одного `$`.

Проверка после `docker compose up`:

```bash
docker compose -f deploy/docker-compose.prod.yml ps
curl http://127.0.0.1:8000/health
```

Оба контейнера должны быть `Up`, не `Restarting`. Если `port 8000 already allocated` — см. раздел «Типичные ошибки» ниже.

### Admitad CSV: FABLE + Aim Clo

После деплоя API (корневой `docker-compose.yml`):

```bash
TOKEN=$(grep '^ADMIN_TOKEN=' /opt/Modish/backend/.env | cut -d= -f2-)

# Создать источники + правила «только одежда» и скачать фиды
curl -X POST http://127.0.0.1:8000/admin/catalog/bootstrap-admitad-csv \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"sync": true}'

# Пересчёт категорий/пола
curl -X POST http://127.0.0.1:8000/admin/catalog/renormalize \
  -H "Authorization: Bearer ${TOKEN}"
```

Коды Admitad CSV: `fable` (25560), `aimclo` (21738), `sportmaster` (26327), `shoppinglive` (25461), `postmeridiem` (25200), `baon` (19982), `mongolshop` (26461), `serginnetti` (26417). Bootstrap без `codes` поднимает все восемь.

### Шаг 5 — импорт каталога

В `backend/.env` задайте `ADMIN_CATALOG_TOKEN`, затем:

```bash
curl -X POST http://127.0.0.1/admin/catalog/alpha-bootstrap \
  -H "Authorization: Bearer ВАШ_ADMIN_TOKEN"
```

Без каталога лента будет пустой («0 вещей»), но кнопка «Обновить» покажет понятное сообщение.

---

## P1 — HTTPS (когда будет домен)

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
cd /opt/Modish   # или /opt/modish — как у вас на VPS
git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

## Типичные ошибки деплоя

| Симптом | Причина | Что сделать |
|--------|---------|-------------|
| `POSTGRES_PASSWORD variable is not set` | В `.env` нет `POSTGRES_PASSWORD` или compose не подхватил файл | Добавить в `backend/.env`, перезапустить compose |
| `variable "i4CyO2e1nllKpr" is not set` | В пароле/секрете есть `$`, Docker воспринял кусок как переменную | Экранировать: `$$` вместо `$`, или убрать `$` из пароля |
| `deploy-db-1 Restarting` | Пустой пароль при первом старте тома или смена пароля без сброса тома | Исправить `.env`; если том создан с пустым паролем — `docker compose down`, удалить volume `pgdata`, поднять заново (данные БД сотрутся) |
| `port 8000 already allocated` | Старый API всё ещё слушает 8000 | `docker ps` и `ss -tlnp \| grep 8000`; остановить старый контейнер/процесс, затем `up` снова |
| Только `deploy-db-1` в `ps`, нет `api` | API не стартовал из‑за порта или БД | Сначала починить БД и порт, потом `up -d --build` |

После успешного деплоя:

```bash
docker compose -f deploy/docker-compose.prod.yml ps
# deploy-api-1  Up
# deploy-db-1   Up (healthy)

curl http://127.0.0.1:8000/health
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
