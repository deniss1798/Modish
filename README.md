# Modish — alpha (каталог + лента)

Мобильное приложение персонального AI-стилиста. **Alpha:** реальный каталог (YML/XML Befree), импорт, лента товаров, affiliate-клики, события и базовые рекомендации.

## Что входит в alpha

- **Flutter:** JWT, подборка товаров (`/feed`), свайпы, карточка с галереей и описанием, обновление товара с сервера в деталях, сохранённые товары, образы, профиль, affiliate-переходы.
- **Backend:** PostgreSQL, импорт YML-каталога, поля товара (group_id, описание, картинки, категория, param), `/feed` с фильтром `source`, `/products`, affiliate click, события `/recommendations/events`, скоринг с бюджетом и просмотрами.

## Запуск Flutter

```powershell
flutter pub get
flutter run
```

На **Android-эмуляторе** по умолчанию уже подставляется `http://10.0.2.2:8000` (доступ к uvicorn на вашем ПК). На **Windows/macOS desktop** и **iOS Simulator** по умолчанию `http://127.0.0.1:8000`.

Другой хост (например, телефон в Wi‑Fi):

```powershell
flutter run --dart-define=MODISH_API_BASE_URL=http://192.168.1.10:8000
```

## Проверка

```powershell
flutter analyze
flutter test
```

## База и backend

Секреты и строка БД задаются **только** в `backend/.env` (шаблон — `backend/.env.example`). Без файла приложение и Alembic завершатся с понятной ошибкой.

```powershell
cd backend
copy .env.example .env
# отредактируйте .env: DATABASE_URL, JWT_SECRET

python -m venv .venv
. .venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Список маршрутов см. `backend/README.md`.

**Импорт каталога Befree:** задайте `ADMIN_CATALOG_TOKEN` и опционально `BEFREE_FEED_URL` в `.env`. Затем `POST /admin/catalog/alpha-bootstrap` и `POST .../sources/{id}/sync` или `sync-upload` (см. `backend/README.md`).

**После импорта — нормализация каталога (P2):**

```http
POST /admin/catalog/renormalize?source=befree
Authorization: Bearer <ADMIN_CATALOG_TOKEN>
```

**Production HTTPS (P1):** см. `deploy/README.md`. Release APK по умолчанию бьёт в `productionApiBaseUrl` из `lib/core/config/api_config.dart` (сейчас VPS). Явно при сборке:

```powershell
flutter build apk --release --dart-define=MODISH_API_BASE_URL=https://api.YOUR_DOMAIN.com
```

**Аналитика (P7):** `GET /analytics/summary?days=7` (нужен admin token) — impressions, clicks, CTR, топ сохранённых/кликов.

**Миграции:** `alembic upgrade head` (таблица `product_impressions`).

## Очистка артефактов

Закройте Cursor/Android Studio, остановите `uvicorn`, затем:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clean_artifacts.ps1
```

Папку `backend\.venv` удалите вручную, если нужна чистая переустановка зависимостей.

## Дальше до продакшена

- OpenAI для анализа, генерации карточек и текстов summary; Redis rate limit; разнесение backend по модулям ТЗ; Riverpod + go_router во Flutter; обработка edge cases и e2e-тесты.
- Отдельные таблицы вариантов SKU, A/B весов рекомендаций, мониторинг импорта фидов.
