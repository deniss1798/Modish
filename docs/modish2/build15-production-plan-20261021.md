# Build15 и свежий каталог: план рабочего переключения

План подготовлен 21–22 октября 2026. После отдельного согласования пользователя
deployment завершён **23 октября в 00:10 MSK**: API build15, схема0028,
четыре источника импортированы, доступ открыт. Подписанный release APK также
собран и передан. Фактический результат, резервная копия, исправление smoke и
оставшиеся этапы: [отчёт переключения](build15-activation-result-20261023.md).
Ниже сохранены исходные доказательства и порядок; старые статусы подготовки
не описывают текущее состояние сервера.

## Зафиксированные артефакты и факты

| Объект | Проверенное значение |
| --- | --- |
| Source commit | `a9b00e78018bfdff3ee6a8e2d458902b7e3a0971` |
| Source archive SHA256 | `5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24` |
| Новый build15 image ID | `sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec` |
| Рабочий build14 image ID | `sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec` |
| Рабочая схема → целевая | `20261013_0025` → `20261015_0028` |
| Fresh snapshot manifest SHA256 | `5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c` |
| Rehearsal run ID | `9c4c8c7fe5a045068cde76d81df12104` |
| Clone proof SHA256 | `a91130f331c9a909116e15a7eb587afde5a27db2fdd04a996d60b54e549733f0` |
| Atomic import proof SHA256 | `12d52089beaf1b3801dc0005740a525977a57317a46ec84b1251cd32dbd0c60a` |
| QA proof SHA256 | `36e1ee48acfa1502529867f040e2a0908d7c112246f7942b97b85adee19e7830` |

Новый image собран на официальном Python 3.12-slim, без наследования старого
build14. Финальный аудит сравнил **все 296 backend-файлов** с manifest архива;
лишних, отсутствующих и изменённых файлов нет, `pip check` прошёл. Проверка
зависимостей и исходников относится именно к указанному immutable image ID.

Рабочий deployment использует Compose project `modish`, service `backend`,
PostgreSQL `modish_postgres`, database `modish`, role `modish_user`, Redis
`modish_redis` и сеть `modish_default`. Labels указывают на
`/opt/Modish/docker-compose.yml`. Шаблон с service `api`/`db` и другой ролью
не является фактической production-конфигурацией.

Read-only диагностика подтвердила причину глобально пустого каталога:
203 006 товаров, 63 994 положительные stock-строки, но все наблюдения старше
72 часов; последнее наблюдение было 12 октября. Это не доказательство ошибки
пользовательских фильтров и не основание ослаблять freshness/size gates.

На новой UUID-копии production backup восстановлен и мигрирован до 0028.
Число строк и нормализованные полные значения **31 таблицы** сохранены с учётом
ожидаемой смены типов/новой revision-колонки. Это проверка конкретного backup,
но не финального backup будущего окна переключения.

Четыре экспорта получены с реальными observation times **21 октября
19:55:41–19:56:17 UTC (22:55:41–22:56:17 MSK)**. На копии завершён один атомарный
импорт 70 163 входных строк: BAON 9 318, Shopping Live 43 402, Serginnetti 17 065,
Post Meridiem 378. Существующие ID сохранены, строки остальных источников
сохранены. Количество входных строк не означает количество подходящих вещей:
часть строк уходит в quarantine, неподтверждённые размеры не становятся точными.
Завершённый QA на копии: 10 профилей, 270 discovery-строк у 9 профилей с полом,
у no-fit — 0; реальный HTTP Style Swipe — 15 лайков, затем 10+10 карточек ленты
без повторных IDs/aliases, 10 брендов и catalogue status `ready`. Внешних сетевых
обращений — 0, синтетические fixture-записи очищены. Точной обуви EU 38 нет,
поэтому wow — 0 с честным объяснением; неизвестные размеры остаются только в
отдельном каталоге. QA занял около 33 секунд. Это не проверка рабочих записей.

## Проверяемые условия рабочего переключения

1. Приложить завершённый [QA десяти профилей и реального Style Swipe](../../scripts/check_catalog_refresh15_postgres.py)
   к deployment-плану: SHA helper/report, exact clone/image/manifest identity,
   дата выполнения и результаты выше. Пустые полные образы допустимы только с
   честной причиной и без обещания наличия точной обуви. В production этот
   пишущий QA helper не запускается.
2. Подготовить отдельный reviewed production runner. Текущий
   [importer](../../scripts/catalog_refresh15_import.py) намеренно принимает
   только UUID test database/role/host; выключать его защиту ради production
   нельзя. Новый runner должен закреплять фактические production identities,
   archive/image/helpers/manifest, список четырёх источников и final backup,
   сохранять единый commit и IDs, иметь ресурсные/SQL/общие timeouts,
   ownership-проверку контейнеров и леджер неопределённого commit без auto-retry.
3. Доказать rollback, совместимый со схемой0028, на копии с реальными данными
   после импорта: копейки, NULL gender и ненулевая recommendation revision.
   Если отдельного рабочего rollback image пока нет, планировать maintenance
   restore только до возобновления пользовательских записей. Не обозначать
   старый build14 как проверенный rollback.
4. Подготовить точный deployment diff для фактического service `backend`,
   private env и reverse proxy; приложить безопасные aggregate-проверки.
   Предъявить пользователю пакет, результаты QA, длительность rehearsal,
   влияние maintenance/import и конкретный rollback-путь для согласования.

До этих gates можно продолжать код, QA, review и подготовку артефактов.
Импорт копии не разрешает импорт production, переключение API или включение timer.

Отдельно подготовлен `scripts/release15/build15_activation_prepare_remote.py`. Это только
read-only Docker metadata и новые private proposal-файлы: закреплённый JSON-план,
копии current Compose/runtime, предложенные override/environment и aggregate
отчёт с SHA плана. Он не выполняет SQL, backup, stop/start, migration, import
или timer. Private environment сохраняет API JWT; import worker получит отдельное
минимальное окружение без API JWT и внешних API-ключей. Proposal не становится
activation runner, а наличие статической approval-фразы в JSON не является
разрешением владельца. Перед выполнением production importer внешний caller
обязан отдельно передать pinned plan SHA и явно одобренный marker; preparer
этого не делает. Rendered Compose config проходит отдельный private review
с сопоставлением фактических ports/mounts/networks/env до запуска.

22 октября read-only preflight подготовил private plan
`a4aa6774f26d469483e6c13cdb2186c5`, SHA256
`f5e49a2c04beca1c488f2cc4f014f3d0ba6c1fa276ee5a1a12224d97e724da5a`.
Live API действительно соединяется с `modish` как `modish_user`; отдельная
read-only сессия подтвердила0025. Старый `POSTGRES_PASSWORD` в metadata контейнера
не равен действующему паролю: это init-настройка существующего PG volume,
а не источник актуальных credentials. Сохраняется действующий API DATABASE_URL,
его hash закреплён в плане, перед stop выполняется реальная read-only проверка.

Nginx использует `http://127.0.0.1:8000`, site — root0644
`/etc/nginx/sites-available/modish` через symlink в `sites-enabled`.
Исходный site SHA256 —
`baf24d87e4812dc9a0d2aff59d5a5ce32dcd1d8b5f6bc65455aeb2aa551799cb`.
Новый отдельный proxy proposal привязывается к immutable plan внешним SHA.
Он добавляет проверку persistent maintenance-флага
`/etc/nginx/modish-build15-maintenance.flag` в существующий SSL server.
API получает только `127.0.0.1:8000`: прежний публичный port8000 позволял бы
обойти nginx maintenance. Флаг не находится в `/run`, чтобы reboot не открыл
доступ в середине migration/import.

Подготовлен исполняемый порядок, который ещё нельзя запускать без approval:

- `scripts/release15/build15_proxy_maintenance_remote.py`: CLI только готовит private
  site/test-main/proxy plan. Применение и снятие fence — отдельные функции
  одобренного executor; nginx test, контролируемые stop/start и TLS
  loopback503/200 проверяются. Перед start подтверждается отсутствие всех
  прежних nginx processes: graceful reload мог оставить старые keepalive routes.
- `scripts/release15/build15_activation_render_remote.py`: до approval выполняет только
  metadata inspect и Compose config, проверяет сохранение runtime и готовит
  private immutable resolved config с loopback ports. Его SHA/report — обязательный
  вход continuation; неожиданные Compose merges не выясняются в maintenance.
- `scripts/release15/build15_activation_final_backup_remote.py`: plan/proxy/writers review
  guards, nginx fence, stop точного API, restart policy `no`, final dump и
  восстановление в новую UUID test DB с полным сравнением строк; оставляет
  maintenance,0025 и API остановленным.
- `scripts/release15/build15_activation_continue_remote.py`: отдельные migration/import,
  private API без опубликованных портов, read-only HTTP smoke, создание public
  API в stopped состоянии с loopback binding, smoke под nginx fence,
  durable writes-open marker и снятие флага в самом конце.
- [Production importer](../../scripts/catalog_refresh15_production_import.py)
  — отдельный worker; guards test importer не ослаблены. Внешний caller
  закрепляет его SHA, image, mounts, ресурсы и one-shot outcome directory.

Все эти имена — подготовленные helpers, не отчёт об их применении. Пятнадцать
offline contracts проверили drift env/ports/network/storage, lifecycle hooks,
полную замену ports вместо merge, повторную интерполяцию `$`, monetary/revision
нормализацию proof, отказ production SQL write в backup utility, actual worker
resources/capabilities, отдельный cleanup budget и закрытие старых nginx workers.

## Окно переключения после конкретного согласования

### Завершённая подготовка на22 октября

Исполнители и offline guards сохранены в [scripts/release15](../../scripts/release15/README.md),
их точные SHA — в [manifest](../../scripts/release15/manifest.json). Git attributes
сохраняют LF на Windows/Linux, чтобы byte pins не менялись при checkout.

- Отдельный неизменённый production importer прошёл реальную репетицию на
  новом изолированном PostgreSQL: все31 исходные таблицы восстановлены, миграция
  сохранила значения,70163 строки импортированы одной транзакцией; IDs и
  внецелевые строки сохранены. Exercise SHA `7600eb3224d4b8584cfe260552cd466c7695941f0b04d13003305593ea0684e2`,
  commit report SHA `564c033dbbaa4d75450252b213ead4934fbe02dbc3dc5433c7d26559d092ef0f`.
- 35 import/download guards действительно прошли в Linux без сети,0skips.
  Host executor guards —74PASS; secret-scanner regression suite —21PASS,
  сканер не нашёл credentials. Это не суммарное число уникальных продуктовых
  сценариев: часть guards повторяется в Windows/Linux.
- Private nginx syntax и реальный второй разбор frozen Compose прошли.
  Подтверждены environment/storage/network и единственный API bind127.0.0.1.
  Proxy plan SHA `7d0611e846af88231f42971bb06ab04631ce8e0fd3fa250f21bfd8550ca087f4`;
  runtime render report SHA `7ce4425bcf1573cc94905bc26ee1a8996bb026566c85f73c4821821d72e8ffdf`.
  Render UUID `a18649cb87794b2b9f84c939134728be`.
- Manual pre-writes recovery подготовлен и независимо проверен: P0/P1 нет,
  26 offline tests PASS; SHA `8056a80eec14397c9a7551098565f30b6d9ac0804863e6d9d45033d35becf842`.
  Public legacy API сохраняет исходный writable rootfs для загрузки фото,
  shadow остаётся read-only. Полный recovery на production не выполнялся.
- [APK проверен на чистом Android-эмуляторе](build15-android-smoke-20261022.md),
  включая поворот,200% текст и три системные кнопки. Это debug QA artifact.

Теперь требуется явное согласование **этого** package для production: nginx
maintenance, final backup+restore proof,0025→0028, четыре источника, API build15
и описанная ветка восстановления только до пользовательских writes. Final
backup нельзя получить заранее вместо backup окна: его исполнитель запускается
после согласования и закрытия writers. Результаты полной реальной репетиции
импорта занимают около14 минут с восстановлением/миграцией; рабочее окно с
дополнительными final proofs может быть дольше. Не обещать переключение без
недоступности API. При истечении72 часов snapshots потребуются новые данные и QA.

Текущий production остаётся build14/schema0025. Timer и постоянная Android
release-подпись пока не включены и не считаются готовыми этим согласованием.

1. **Preflight без записи.** Повторно сверить текущие container/image IDs,
   revision0025, Compose labels, volume/сетевые привязки и состояние API/PG/Redis.
   Любое отличие от согласованного baseline требует review до продолжения.
   Проверить свободное место для final dump, его восстановления и images,
   синхронизацию UTC и достаточную память. Проверить hashes всех входных файлов.
   Нельзя повторно выдавать старый CSV за свежий: сохраняются реальные
   observation times. Если самый ранний snapshot старше72 часов, получить новые
   четыре экспорта и повторить импорт/QA на копии до production-записи.
2. **Закрыть все пути записи.** Проверить private nginx proposal, применить
   persistent maintenance-флаг и точный site, выполнить nginx test,
   контролируемые nginx stop → zero old processes → start и получить TLS
   loopback503. В maintenance будет короткая недоступность ingress, затем503.
   Дождаться завершения активных запросов,
   остановить старый `backend` и отключить его автоматический restart.
   Зафиксировать и остановить подтверждённые фоновые writers/import jobs,
   если они найдены; проверить активные DB sessions/transactions. Один внешний
   maintenance-экран не останавливает фоновый импорт или прямой API-доступ.
   Существующие PG/Redis/volumes не пересоздавать и не очищать.
3. **Final backup под закрытыми writers.** Новый private UUID-каталог0700,
   dump0600, `pg_dump -Fc --no-owner --no-acl`, SHA256 и baseline counts/row proofs.
   Резервировать также фактические private env/Compose/proxy settings и storage
   mounts; секреты остаются на сервере и не попадают в отчёт/Git/Downloads.
   Восстановить final dump в ещё одну отдельную test DB/role и проверить
   читаемость и целостность. Если final restore не доказан, не мигрировать.
4. **Миграция отдельно от API.** Точным build15 image запустить одноразовый
   `python -m alembic upgrade 20261015_0028`, с явным entrypoint `python`,
   ограниченными CPU/memory/timeouts и закреплённым private DB environment.
   Проверить head0028, `NUMERIC(14,2)` у денежных колонок, nullable gender без
   default, revision NOT NULL/default0, сохранение исходных IDs/строк/связей.
   Смена типа цены не меняет рубли на копейки. При ошибке не продолжать импорт.
5. **Каталог.** Запустить только отдельно принятый production importer над
   закреплёнными четырьмя snapshot-файлами. До первой записи проверяются все
   файлы, manifest, часы, объём, IDs и source identity. Одна транзакция для
   четырёх источников, отключение missing SKU только внутри них; source/stock
   clocks равны observation times. При timeout/потере связи сначала проверить
   ledger и фактический commit; слепой повтор запрещён. Не переиспользовать
   sync CLI, который commit делает отдельно на каждый источник.
6. **Запуск API в maintenance.** Сначала private API без host ports с read-only
   DB options; затем создание целевого `backend` без start и проверка runtime.
   Полный resolved Compose config заменяет список ports целиком на
   `127.0.0.1:8000`, чтобы merge не сохранил прежний внешний8000.
   Новый API из точного image ID запускается
   напрямую через `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`,
   `ENTRYPOINT []`. Default `scripts/entrypoint.sh` выполняет auto-migration и
   здесь не используется. Сохраняются текущие private DATABASE_URL/JWT/session
   expiry/storage/proxy settings. JWT уже валиден и не меняется: существующие
   сессии не должны сбрасываться при deployment. Явно задать `APP_ENV=production`,
   `CLOSED_ALPHA_ENABLED=false`, `STYLE_VISUAL_ENABLED=false`,
   `ALLOW_DEV_ANALYZE=false`. Проверить значения булевых политик и корректность
   DB/JWT в памяти, наружу вывести только fixed pass/fail. Не монтировать старую
   `.env` в `/app` и не полагаться только на `PYTHON_DOTENV_DISABLED`.
7. **Приёмка до открытия записи.** Строгий HTTP200 readiness и versionbuild15,
   отдельная проверка head0028/денежных типов, catalogue status, свежих SKU,
   брендов, категорий обувь/верхняя одежда и существования товара по ID.
   Readiness делает DB ping и не доказывает схему или качество каталога.
   Полный пишущий onboarding smoke уже доказан на final-copy; в production
   создавать fixture-пользователя только если это явно входит в согласованный
   runner с ограниченным owner-ID cleanup. Не использовать реальные учётные
   записи пользователей как тестовые и не очищать общий Redis ради smoke.
8. **Открыть доступ.** Только после всех pass ещё раз проверить nginx fence,
   записать durable writes-open marker, удалить свой persistent flag и получить
   TLS200/versionbuild15 через nginx. Вернуть
   согласованные writers, наблюдать5xx/401/latency и catalogue freshness.
   Зафиксировать итоговые image/schema/manifest IDs, время открытия и результат
   smoke. Факт возобновления записи переводит rollback в отдельную ветку ниже.

## Откат: до и после новых пользовательских записей

| Момент отказа | Действие |
| --- | --- |
| До migration/import | Сохранить артефакты, убрать только свои candidate-процессы, вернуть старый backend при подтверждённой неизменности0025. |
| После migration/import, пользовательские writers ещё закрыты | Совместимый build15 API rollback, если он проверен. Иначе maintenance restore из **final backup**, затем старый image/config на восстановленной0025; проверить восстановление до открытия. |
| После открытия пользовательских writers | Не восстанавливать старый dump автоматически. Проверенный совместимый API rollback на0028 либо maintenance и forward-fix; последствия возможного restore отдельно согласовать, поскольку появились новые данные. |
| Commit import неизвестен | Сохранить maintenance и ledger, проверить фактическое состояние; не повторять import и не запускать downgrade автоматически. |

Старый build14 не является безопасным fallback на0028: прежний API может
усекать Decimal до int, старый Alembic не знает head0028, есть NULL gender и
новый revision protocol. Downgrade намеренно отклоняет копейки, числа внеint32
и NULL gender. Ошибку downgrade нельзя обходить исправлением пользовательских
данных или округлением цен.

## Регулярное обновление каталога — отдельный принимаемый этап

В проверенных21 октября root/system cron и enabled system timers не найден
подтверждённый Modish catalogue job. Проверено12 cron-файлов/16 entries и
20 timers/20 связанных services, без превышения bounds/ошибок metadata.
Это не покрывает user timers, non-root crontabs, Docker schedulers, вложенные
wrapper-команды, at jobs и произвольные services. Перед включением новой задачи
проверить эти места и исключить конкурирующих writers.

Предлагается **один systemd service + один timer раз в12 часов** с jitter и
межпроцессным lock, без новой очереди или отдельного orchestration-сервиса.
Период оставляет запас до freshness gate72h; он не меняет сам gate.
За первый принятимый запуск следует manual run с сохранённым отчётом, затем
разрешается timer. Сейчас timer не создан и не включён.

Требования к задаче, которые нужно реализовать и проверить до включения:

- Pinned image/helpers, новый run ID/private0700 workspace на каждый запуск.
  Fixed четыре HTTPS export; TLS включён, redirects/proxy overrides запрещены.
  Credentials передаются stdin/private file0600, URL/env/ошибки не печатаются.
- Downloader отделён от DB network/env; на каждый фид256 MiB,600s hard limit;
  общий downloader2500s, memory512 MiB/CPU0.5. Complete manifest только после
  четырёх полных файлов. Ошибка источника прекращает запуск до DB-записи.
- Importer только на DB network, cap-drop/read-only rootfs, memory1536 MiB/CPU1,
  deadline7200s, bounded statements120s/lock5s; import в одной транзакции.
  Не запускать4 независимых `sync_partner_feed` CLI с раздельными commit.
- Предел строк/резкое исчезновение ID/hash/source/observed_at проверяются до
  записи. Unknown sizes остаются discovery-only; price/gender gates сохраняются.
  Реальный `observed_at` не обновляется временем обработки старого файла.
- Lock и one-shot ledger запрещают overlap/replay. Watchdog останавливает
  только свой проверенный container ID; uncertain commit требует review.
- Aggregate report: source codes, counts, свежесть, timestamps, hashes, outcome.
  OnFailure и freshness-monitor показывают сбой; возраст48h — ранний сигнал,
  72h — каталог честно исключает stale товары. Не изображать новый ассортимент
  переустановкой timestamps. Никаких уведомлений пользователям от имени системы
  без отдельно согласованного канала.
- Согласовать retention private dumps/CSV/reports и отдельный bounded cleanup;
  при первой ошибке не удалять доказательства. До cleanup проверять ownership
  и абсолютный путь; никаких `docker system prune` или удаления общих volumes.

## Граница публичного выпуска

Server readiness, catalogue freshness и public APK — разные результаты.
Постоянный Android release key создан по отдельному выбору пользователя;
certificate, applicationId и установка подписанного APK проверены. Переход с
прежней debug-подписи требует переустановки. Регулярный refresh, внешняя копия
ключа и публикация в магазинах остаются отдельными этапами.
Подробности: [результат](build15-activation-result-20261023.md),
[публичный выпуск](public-release-plan-20261020.md),
[deployment safety](build15-deployment-safety.md),
[migration proof](build15-migrations.md),
[fresh clone helpers](catalog-refresh15-rehearsal-20261021.md).
