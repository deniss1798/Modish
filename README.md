# Modish (предрелиз v1)

Мобильное приложение персонального AI-стилиста по ТЗ v1

## Что сделано для предрелиза

- **Flutter:** splash и восстановление сессии (JWT в `flutter_secure_storage`), таймауты сети при boot, нижнее меню в порядке ТЗ (Подборка → Рекомендации → Сохранённое → Профиль), свайпы на подборке, загрузка фото (галерея + камера, превью, удаление, лимит 5 MB на клиенте), экран рекомендаций по полям summary, сохранённое с API и фильтрами, профиль с billing, `view_details` при открытии деталей.
- **Backend:** PostgreSQL + SQLAlchemy + Alembic; JWT; лимиты Plus (1 анализ фото / месяц, 5 генераций пачек / месяц); `saved_recommendations`, `user_preferences`, `user_recommendation_summaries`, `user_limits`; эндпоинты из раздела 14 ТЗ (включая `generate`, `saved`, `billing`, `summary/rebuild`, `style-profile/me`); пересборка summary после каждых 10 событий feedback; мок AI для анализа и генерации (без OpenAI до продакшена).

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

## Очистка артефактов

Закройте Cursor/Android Studio, остановите `uvicorn`, затем:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clean_artifacts.ps1
```

Папку `backend\.venv` удалите вручную, если нужна чистая переустановка зависимостей.

## Дальше до продакшена

- OpenAI для анализа, генерации карточек и текстов summary; Redis rate limit; разнесение backend по модулям ТЗ; Riverpod + go_router во Flutter; обработка edge cases и e2e-тесты.
- Подключение реальных товаров и каталогов (SKU, партнёрские фиды, экран «что купить» поверх текущих текстовых рекомендаций).
