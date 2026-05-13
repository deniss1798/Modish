# Modish Backend (предрелиз)

FastAPI + PostgreSQL + SQLAlchemy + Alembic. JWT и bcrypt. Лимиты и сохранения по ТЗ v1.

## Запуск

Обязательный файл **`backend/.env`** (скопируйте из `.env.example`). В нём как минимум:

- `DATABASE_URL` — строка подключения PostgreSQL
- `JWT_SECRET` — секрет подписи токенов

Опционально: `JWT_EXPIRES_HOURS` (по умолчанию 24).

```powershell
cd backend
copy .env.example .env
# заполните .env

python -m venv .venv
. .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

Если команда `alembic` «не найдена», всегда используйте **`python -m alembic`** (тот же интерпретатор, что и у активированного venv).

Переменные **не** читаются из оболочки для этих двух ключей — только из `.env` (при старте импортируется `app.config`).

## API (сверено с ТЗ §14)

**Auth / пользователь**

- `POST /auth/register`
- `POST /auth/login`
- `GET /users/me`

**Style profile**

- `PATCH /style-profile/target`
- `GET /style-profile/me`
- `PATCH /style-profile/me` — частичное обновление `profile_json` / `style_target`, пересборка summary
- `POST /style-profile/analyze` — multipart `photo` (jpg/png/webp, до 5 MB), лимит Plus, мок AI JSON

**Recommendations**

- `POST /recommendations/generate` — тело `{ "type": "outfit", "count": 1..10, "scenario": "daily"|"office"|"evening" }`, лимит пачек
- `GET /recommendations/feed`
- `GET /recommendations/{id}`
- `POST /recommendations/{id}/feedback` — `like` / `dislike` / `save` / `unsave` / `view_details`; обновление `user_preferences`; save/unsave в `saved_recommendations`; каждые 10 событий — пересборка summary
- `GET /recommendations/saved`

**Summary**

- `GET /recommendations/summary` — блоки для экрана «Рекомендации»
- `POST /recommendations/summary/rebuild`

**Billing**

- `GET /billing/status` — `plan`, `status`, `trial_ends_at`, `is_plus_available`

**Служебное**

- `GET /health`

Redis и Docker в этом предрелизе не обязательны; моки AI в `app/business.py` / `app/main.py`.

## Alpha: каталог (admin, `ADMIN_TOKEN`)

- `POST /admin/catalog/alpha-bootstrap` — источник `befree` (один раз); URL из `BEFREE_FEED_URL` в `.env` (опционально)
- `GET|POST|PATCH /admin/catalog/sources` — источники
- `POST /admin/catalog/sources/{id}/sync` — импорт по `feed_url`
- `POST /admin/catalog/sources/{id}/sync-upload` — загрузка файла XML/YML
- `GET /admin/catalog/sync-runs` (опционально `?source_id=`), `GET /admin/catalog/sync-runs/{run_id}`

**Модель alpha:** строка `products` = один SKU (оффер); отдельная таблица `product_variants` не используется — вариант в колонках и `feed_raw_json`.
