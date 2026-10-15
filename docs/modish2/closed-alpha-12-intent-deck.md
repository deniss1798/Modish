# 1.2 — Style Swipe и создание одиночного поискового запроса

15 октября 2026. Продолжение [проверенной ленты](closed-alpha-12-feed-publication.md).
Изменения только локальные. Весь1.2 ещё открыт: остаются Intent edits/outfit
composition, WOW и legacy outfit paths. Следующий пункт1.3 не начинался.

## Style Swipe

HTTP GET `/onboarding/style-swipe` освобождает auth connection и считает колоду
в отдельной READ ONLY / REPEATABLE READ Session. Подбор кандидатов не блокирует
лайки. Замороженный proposal содержит финалистов, revision/profile signature
и отдельный fingerprint текущей колоды. Allocation не меняет вкус, поэтому
нельзя использовать один Taste revision для двух параллельных GET.

Публикация проверяет эти версии, текущее product evidence/правила/размер/наличие
и сохраняет IDs атомарно. Каталог и exclusion-side identity блокируются до User.
При конфликте — один повтор; второй409 не оставляет частичную колоду.
Закешированная карточка, ставшая алиасом уже оценённой вещи после импорта,
удаляется и заменяется допустимой. Те же identity rules применены в старом helper.

## Одиночный поисковый запрос

POST `/shopping-intents` для single-item теперь состоит из короткого admission,
read-only prepare/rank и короткой публикации. Для существующего unique
`(user_id, request_id)` хранится namespace `generation_v1` внутри context_json:
pending/ready, generation, claim_id, lease_until, intent_version. Новых таблиц
и зависимостей нет. Обычный успешный ответ сохраняет статус200 и форму API.

Повтор с тем же текстом во время pending получает понятный409/Retry-After2;
другой текст с тем же ключом отклоняется. После120с pending можно перехватить
новой generation. Старый worker после перехвата не может опубликовать результат
или освободить чужой claim. Истечение lease само по себе не доказывает остановку:
завершить может текущий claimant, пока его не сменил другой.

Публикация проверяет claim/version/ownership/active/expiry, личную revision,
профиль, constraints/exclusions и текущее evidence/admission финалистов.
Результат, ready и generation metrics коммитятся вместе. Только personal/catalog
конфликт разрешает второй расчёт; смена Intent version/generation не перезаписывается.
При исчерпании двух попыток освобождается только собственный текущий lease.

Готовый POST replay и HTTP GET перепроверяют только уже выданные IDs. Они не
ранжируют новый каталог, не подменяют всё ещё допустимые вещи и не повторяют
generation metrics. Pending/старый незавершённый context не объявляется пустым
ready. Прямой старый service helper сохранён для совместимости. Неопределённый
результат commit не запускает слепой повтор: persisted ready восстанавливается
тем же request_id. Проверены ошибки до и после фактического commit.

Существующая Intent admission policy сохранена; feed source rules/глобальные
feed hidden-фильтры не добавлены неявно. Общий `recommendation_publication`
переиспользует fingerprint и Product/identity locks. Source/Rule locking включён
для feed/deck, отключён для Intent, который эти правила не использует.
Outfit create/refine/replace пока сохраняют прежний защищённый расчёт.

Flutter показывает понятные pending/conflict сообщения. Ручной повтор того же
текста сохраняет request_id; автоматической бесконечной отправки нет.

## Приёмка среза

- Полный backend: **695 tests /93,813с, OK**; 671 выполнен, 24 opt-in PG
  пропущены в обычном запуске.
- Отдельно все **24 PostgreSQL tests /35,521с, OK**: feed12, revision3,
  single-item Intent6, deck3. Проверены реальные HTTP запросы, независимые
  соединения, RR/READ ONLY, feedback до снятия ranking barrier, параллельные
  GET/POST, same-key pending/replay, takeover, bounded conflicts, nonempty
  выдача в изменённом бюджете, отсутствие частичных metrics/allocations.
- SQLite: 15 новых Intent tests и7 deck tests, плюс существующие compatibility
  suites; lifecycle, immutable DTO, rollback/commit faults, honest empty,
  quality/exclusion conflicts и отсутствие writes во время ranking.
- Flutter: **240 tests /1:01, OK**, полный suite. Analyzer: **No issues /3,7с**.
- Credential guard: **0 findings**; формат Dart и whitespace check пройдены.

Общий PG fixture helper сверяет `.tmp` data directory и порт до schema writes,
создаёт только собственные уникальные схемы и регистрирует cleanup сразу.
Одноразовый кластер остановлен, postmaster.pid отсутствует. Основная служба
PostgreSQL и рабочий сервер не использовались. Production p95 не измерен.
