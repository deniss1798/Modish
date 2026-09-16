# Реестр Modish 2.3 — исходная версия

Проверка 12.09.2026. Base/local HEAD и удалённый main: `da5a0be1ae96b9656ede7d97dee3799ff3b4c5ca`. Сервер и его схема: НЕ ПРОВЕРЕНЫ.

Ниже исходные замечания документа, а не утверждения о новых исправлениях. Совпадение дерева подтверждает применимость ссылок; исторические воспроизведения не названы повторными запусками. Статусы изменений фиксируются в current_state.md.

## F01

F01 / P0 / ФАКТ расположения, НЕИЗВЕСТНО состояние отзыва. В публичном дереве и истории есть credential-подобное значение DeepSeek в backend/.env.example и встроенный access code в backend/app/integrations/admitad/source_presets.py. Их значения не воспроизводятся. Считать раскрытыми до подтверждения отзыва; убрать из конфигурационных примеров, вынести текущие значения в защищённую конфигурацию. Удаление строки не отзывает доступ. Основание: S00.

**Base:** открыто. **Local:** известные значения удалены; CI guard и маскирование добавлены. Отзыв у провайдеров не подтверждён. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S00: `backend/.env.example; backend/app/integrations/admitad/source_presets.py`

## F02

F02 / P1 / РИСК. Feed downloader принимает admin-configured URL и follows redirects; media proxy проверяет начальный host, но автоматически следует перенаправлениям. Для фида путь требует права администратора; для media маршрут публичный. Это разные предпосылки угрозы. Нужны ограниченные источники, проверка каждого разрешённого перехода, сетевые ограничения и лимиты потока. Живая эксплуатация не выполнялась. Основание: S02, S09.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S02: `backend/app/services/catalog/feed_import_service.py`
- S09: `backend/app/api/media_proxy.py`

## F03

F03 / P1 / ФАКТ реализации. Лимит media в 12 MiB проверяется после получения всего тела; фид также загружается целиком. Защита памяти должна срабатывать во время чтения, включая распаковку сжатого ответа. Для XML дополнительно ограничить структуру и запретить ненужные конструкции. Основание: S02, S09, S17.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S02: `backend/app/services/catalog/feed_import_service.py`
- S09: `backend/app/api/media_proxy.py`
- S17: `backend/app/integrations/admitad/feed_parser.py`

## F04

F04 / P1 / ВОСПРОИЗВЕДЕНО. _int_price('3999.00') = 399900; '3999.50' = 399950; '-100' = 100; числовое 3999.5 = 3999. Разные входные типы означают разные цены. Исправить денежный контракт и восстановить данные из достоверного источника. Основание: S01.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S01: `backend/app/services/catalog/feed_row_mapper.py`

## F05

F05 / P1 / ВОСПРОИЗВЕДЕНО. Пользователь S и товар только XL проходят product_passes_size; compute_fit_score добавляет «Есть ваш размер». Пользователь L и M-only также проходят. Проверка максимального размера не доказывает наличие нужного. Основание: S03, S04. В f15691f исправлен буквенный случай и пояснение; в GitHub main и на сервере исправление не подтверждено.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S03: `backend/app/services/feed_filters.py`
- S04: `backend/app/services/mie_scoring.py`

## F06

F06 / P1 / ВОСПРОИЗВЕДЕНО. Empty sizes проходит текущий фильтр; иноязычная/числовая система без letter index также не даёт точной проверки. Unknown — допустимая характеристика данных, но не основание утверждать наличие или посадку. Основание: S03, S20. Патч убирает ложное положительное свидетельство, но сохраняет просмотр unknown; полный размерный контракт остаётся открытым.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S03: `backend/app/services/feed_filters.py`
- S20: `backend/app/catalog_normalize.py`

## F07

F07 / P1 / ВОСПРОИЗВЕДЕНО на helper. Product с currency=USD проходит product_is_feed_eligible; денежные сравнения ниже используют числовую цену без общего денежного типа. Не установлено, что такие товары есть в production. Для RUB-альфы явно ограничить валюту. Основание: S05, S03, S15.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S03: `backend/app/services/feed_filters.py`
- S05: `backend/app/services/catalog/catalog_quality.py`
- S15: `backend/app/models.py`

## F08

F08 / P1 / ВОСПРОИЗВЕДЕНО. После каталога из двух предложений импорт только одного деактивирует второе. Сервис не знает, полный ли это снимок. При реальном частичном фиде такое поведение может скрыть исправные товары. Основание: S02.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S02: `backend/app/services/catalog/feed_import_service.py`

## F09

F09 / P1 / ВОСПРОИЗВЕДЕНО. HTML с сообщением временной ошибки может завершиться status=ok и нулём строк. _finalize_run обновляет source.last_sync_at при ok. Это риск ложной свежести источника. Полностью пустой результат не деактивирует все товары автоматически: код делает deactivation только при непустом seen_external. Основание: S02.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S02: `backend/app/services/catalog/feed_import_service.py`

## F10

F10 / P1 / РИСК. В строковом цикле sync выполняется SELECT на предложение; исключения поглощаются без savepoint/rollback. Ошибка БД может испортить состояние транзакции, а параллельные sync одного источника — конкурировать. Нужны проверки PostgreSQL и явная граница публикации. Основание: S02.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S02: `backend/app/services/catalog/feed_import_service.py`

## F11

F11 / P1 / ФАКТ пути. ProductImpression записывается по результату ответа; вторичный Flutter refresh дублирует feed. Это серверные выдачи, а не реальные просмотры. Старые CTR нельзя объединять с будущими viewport-метриками без разрыва версии определения. Основание: S06, S07, S08.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S06: `backend/app/api/products.py`
- S07: `lib/app.dart`
- S08: `backend/app/services/product_analytics_service.py`

## F12

F12 / P1 / ВОСПРОИЗВЕДЕНО. Два одинаковых like создают два события, event_strength = 4.0; общей idempotency нет. Flutter повторяет запрос при некоторых сетевых ошибках независимо от метода. Повтор может удвоить сигнал после уже успешного commit. Основание: S10, S11.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S10: `backend/app/api/recommendations.py`
- S11: `lib/core/network/api_client.dart`

## F13

F13 / P1 / ВОСПРОИЗВЕДЕНО. meta.algorithm_version='client-spoof' сохраняется: setdefault не заменяет присланное значение. Авторитетные поля должны браться из серверного контекста выдачи. Основание: S10.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S10: `backend/app/api/recommendations.py`

## F14

F14 / P1 / ВОСПРОИЗВЕДЕНО. Клиентский post_purchase_positive принимается с weight=15 без подтверждённой покупки. Он может влиять на профиль как сильный сигнал. Это не подтверждённый revenue или post-purchase ground truth. Основание: S10, S12.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S10: `backend/app/api/recommendations.py`
- S12: `backend/app/services/recommendation_config.py`

## F15

F15 / P1 / ВОСПРОИЗВЕДЕНО. Event с несуществующим outfit_id принимается. Невалидный product_id при включённых SQLite foreign keys приводит к необработанному IntegrityError в прямом вызове endpoint. Это дефект валидации и целостности событий; из него не следует доказательство чтения чужого образа. Основание: S10, S15.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S10: `backend/app/api/recommendations.py`
- S15: `backend/app/models.py`

## F16

F16 / P1 / ВОСПРОИЗВЕДЕНО маршрутизацией. /products/recommended сопоставляется с /products/{product_id}. Статический route зарегистрирован позже динамического. Реальное поведение маршрутизатора соответствует документации Starlette. Основание: S06, E01.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S06: `backend/app/api/products.py`

## F17

F17 / P1 / ФАКТ. Affiliate endpoint проверяет главным образом существование и is_active, затем пишет click. Проверки availability, выбранного размера и контекста слабее общей рекомендуемой политики. Click_id создаётся после вычисления URL и не встраивается в него. Основание: S13, S14.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S13: `backend/app/api/affiliate.py`
- S14: `backend/app/services/catalog/affiliate_link_service.py`

## F18

F18 / P1 / ФАКТ. Reachable onboarding создаёт M без ответа пользователя. Изменение поля на nullable в одной модели не исправит все fallback-значения и старые записи: нужна provenance-миграция и проверка клиента. Основание: S07, S16, S15.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S07: `lib/app.dart`
- S15: `backend/app/models.py`
- S16: `backend/app/services/onboarding_service.py`

## F19

F19 / P1 / ФАКТ. Login/register исключены из rate limit; регистрация делает несколько commit до окончательного ответа. Внешняя альфа требует ограничения злоупотреблений и атомарного создания обязательных данных. Риск не подтверждён атакой на production. Основание: S18.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S18: `backend/app/api/auth.py; backend/app/middleware.py`

## F20

F20 / P1 / ВОСПРОИЗВЕДЕНО. При синтетической ошибке БД /health возвращает HTTP 200 с degraded; Flutter считает degraded успешным. Нужны раздельные liveness/readiness и корректная обработка состояния. Основание: S19, S11.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S11: `lib/core/network/api_client.dart`
- S19: `backend/app/main.py`

## F21

F21 / P1 / ФАКТ документов и кода. Compose-комментарии используют ADMIN_CATALOG_TOKEN, endpoints — ADMIN_TOKEN. Service env_file не является автоматически файлом для подстановки ${POSTGRES_PASSWORD} в compose-модель. Команда должна явно задавать нужный --env-file или согласованный shell environment. Dockerfile не задаёт non-root user. Основание: S21, E06.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S21: `deploy/docker-compose.prod.yml; backend/Dockerfile; backend/scripts/entrypoint.sh`

## F22

F22 / P2 / ФАКТ. load_user_taste_feature_map читает сохранённые score/confidence без применения decay на момент scoring; decay есть в других операциях User Twin. Долго не затрагиваемые признаки могут сохранять старый вес. Нужна единая временная модель. Основание: S04, S22.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S04: `backend/app/services/mie_scoring.py`
- S22: `backend/app/services/user_twin_service.py`

## F23

F23 / P2 / ФАКТ. Hash embeddings, ограниченный Python scan, ORDER BY random() и per-item обращения создают масштабные ограничения. Их стоимость нужно измерить до выбора Qdrant, Redis или новой модели. Наличие этих конструкций не доказывает неприемлемую latency текущей альфы. Основание: S23, S24.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S23: `backend/app/services/embedding_service.py`
- S24: `backend/app/services/candidate_retrieval_service.py`

## F24

F24 / P1 для нового outfit-контракта / ФАКТ. Total budget отсутствует как intent-поле. После первых найденных комбинаций цикл шаблонов прекращается; это может подавлять one_piece. Wrapper логирует exception, но пустой v2-результат переходит к legacy без такого же явного объяснения. Основание: S25, S26.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S25: `backend/app/services/outfit_engine_v2.py`
- S26: `backend/app/services/outfit_service.py`

## F25

F25 / P2 до эксперимента / ФАКТ. Offline benchmark сравнивает различные pipeline; ranking_v3 — не чистый popular control, он использует liked categories/brands и другой retrieval. Синтетический тест подтверждает работу механизма, но не преимущество MIE на независимых будущих пользовательских предпочтениях. Основание: S27.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S27: `backend/app/evaluation/offline_benchmark.py; backend/tests/recommendation_benchmark/test_offline_benchmark.py`

## F26

F26 / P1 для публичного UX / ФАКТ. Экран Plus обещает функции и показывает цену, но оплаты нет. Тексты первого экрана и ошибок также местами раскрывают внутренние причины вместо понятного следующего действия. До альфы убрать неподтверждённые коммерческие обещания. Основание: S28, S07.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S07: `lib/app.dart`
- S28: `lib/features/subscription/plus_screen.dart`

## F27

F27 / P1 / ВОСПРОИЗВЕДЕНО при подготовке патча. Legacy outfit service отдельно получает товары из каталога и добирает слоты, обходя size-filter основного движка. Проверка только feed или v2 не закрывает этот путь. В f15691f добавлены проверки при первоначальном чтении, доборе и последнем резервном подборе. Регрессии покрывают v2 и legacy; публикация и deployment отсутствуют. Основание базового пути: S29.

**Base:** открыто. **Local:** открыто. **Remote:** открыто. **Deployed:** базовый main установлен в D00; исправление не включено.

- S29: `backend/app/services/outfit_service.py`

