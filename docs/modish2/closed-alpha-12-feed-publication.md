# 1.2 — расчёт и публикация товарной ленты

15 октября 2026. Проверен следующий срез после `a53d572`.
Production, каталог рабочей базы и APK не обновлялись.
**Весь пункт 1.2 остаётся открытым:** Intent, deck/WOW и генерация образов
сохраняют защищённые прежние транзакции до отдельного преобразования.

## Изменения

Все три HTTP-входа `/feed`, `/products/recommended`, `/recommendations/feed`
используют отдельный `feed_session`. Auth Session закрывается до получения
следующего соединения. Инициализация отсутствующих профилей отделена от
ранжирования. PostgreSQL prepare выполняется в READ ONLY / REPEATABLE READ:
история, исключения, поиск кандидатов и scoring не держат write-lock владельца.
Через границы Session передаются только scalar/tuple DTO, без ORM/lazy связей.
До100 финалистов и до400 cache IDs; исключения не усекаются ради заполнения.

Публикация сначала берёт Source → Rule → Product SHARE-lock в стабильном
порядке, затем короткий User lock. Product locks покрывают как финалистов,
так и товары, чья идентичность формирует исключения: обновлённая скрытая вещь
не должна вернуться алиасом неизменившегося финалиста. История и thin identity
projection читаются вне User lock; большие merchant blobs скрытых SKU не нужны.
Для ожиданий PostgreSQL заданы lock_timeout750ms / statement_timeout2000ms.

Под User lock проверяются revision, малый профильный граф, fingerprint товаров,
актуальные правила, точный размер, бюджет, фильтры и текущее наличие. Наличие
проверяется на время публикации: длительный расчёт не продлевает наблюдение фида.
При конфликте proposal отбрасывается и пересчитывается максимум один раз.
Второй конфликт — понятный409, без частичных impressions/cache/issued IDs.
Удалённый между фазами аккаунт получает401. Неопределённый результат commit
не повторяется автоматически.

Выдача IDs и ProductImpression означают доставку, не реальный просмотр.
Они не обучают Taste и не расходуют первые20 ActivationExposure. Параллельным
prefetch по-прежнему разрешено пересекаться; отдельная delivery reservation
здесь не вводилась. Публичная форма ответа сохранена. Старый `_feed_scored_items`
оставлен для внутренних совместимых вызовов/тестов, HTTP его больше не вызывает.

SourceRule admin writers и Admitad bootstrap явно блокируют Source перед Rule,
включая добавление нового правила. Только Source lock недостаточен для identity:
ручной/admin импорт может менять изображения без него, поэтому нужны Product locks.
Legacy tag writer также подключён к общей owner revision.

Устранён найденный при review lost-update: product feedback и прямой exposure
теперь входят в owner mutation **до** чтения/изменения ранее загруженных Taste,
state и issued attribution. Commit-free применение события отделено от commit
и прежней последующей milestone-генерации. Два принятых лайка не затирают друг друга.

## Проверки

- Полный backend: **664 теста /81,170с, OK**; 15 opt-in PostgreSQL тестов
  пропущены в стандартном запуске, остальные649 выполнены.
- Все **15 PostgreSQL tests /22,314с, OK** отдельно: 12 feed +3 foundation.
  Независимые соединения и барьеры, HTTP `/feed` + `/recommendations/events`,
  feedback и изменение бюджета завершаются до снятия ranking barrier;
  ready response не содержит устаревшие SKU. Проверены два конфликта,
  импорт цены, новое правило, source/product/hidden-side ожидания до User lock,
  реальное ожидание writer в pg_stat_activity и сохранение обоих лайков.
- SQLite: неизменяемые DTO, отсутствие SQL writes при ranking, три HTTP aliases,
  профиль/каталог/identity конфликты, истечение stock только из-за времени,
  NULL gender/удалённый owner, запросные фильтры, cache limits, legacy parity
  на положительном каталоге при одинаковом as_of. Отдельная отрицательная
  first-session oracle остаётся на более узком activation-сегменте.
- Flutter: **238 tests, OK**, полный suite; analyze: **No issues found /3,5с**.
  Добавлена понятная409-копия с действием «обновить ленту».
- Credential guard: **0 findings**; whitespace check пройден.

PG проверялся только на одноразовом workspace-кластере18.3 /127.0.0.1:55439,
с проверкой `SHOW data_directory` перед schema writes. Каждый test использует
свою schema; все удалены, fixture остановлен, postmaster.pid отсутствует.
Основная служба PostgreSQL не использовалась. Production p95, скорость на
реальном каталоге и физическом телефоне этим прогоном не измерялись.

## Дальше внутри1.2

Первым преобразован feed: у него нет persisted pending/replay протокола Intent.
Теперь single-item Intent create с generation/lease и idempotent publication;
затем refine/replacement/composition, deck/WOW и legacy outfit paths.
Ранжирование этих путей не объявляется исправленным результатами feed tests.
Следующий пункт ТЗ1.3 пока не начинается.
