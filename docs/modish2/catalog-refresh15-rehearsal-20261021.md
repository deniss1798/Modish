# Свежие источники на новой копии build15

Изолированная проверка завершена 22 октября 2026. Свежие четыре фида прошли
реальный импорт актуальным build15 pipeline на копии БД; затем неизменённый
production importer проверен на ещё одном отдельном PostgreSQL. Production API,
его схема и рабочие товары этим экспериментом **не изменены**. Эта приёмка
не означает, что build15 уже развёрнут в production, и не разрешает deployment.

При локальной подготовке helpers серверные команды не выполнялись. Описанные
ниже реальные загрузки, SQL и контейнерные проверки выполнены отдельно root
с закреплёнными артефактами и отчётами. Dump, credentials и raw rows остаются
приватными серверными файлами и в этот документ/Git не включаются.

## Зафиксированная исходная точка

Root получил её отдельным READ ONLY запросом 21 октября 2026:

- Рабочий backend image
  `sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec`.
- PostgreSQL container ID
  `987af124c33aa2984c6d4379417036f4278f3024ba75c6f145dcd29838da667e`,
  database `modish`, role `modish_user`, revision `20261013_0025`.
- 203006 товаров; 63994 строки с положительными stock flags, все наблюдения
  старше72 часов. Последнее наблюдение четырёх источников —12 октября.
- Текущий проверенный build15 archive SHA256
  `5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24`,
  source commit `a9b00e78018bfdff3ee6a8e2d458902b7e3a0971`, head0028.

Это исходное состояние рабочего сервера на 21 октября. Актуальность новых
наблюдений проверена последующими загрузками и импортом на копии; старые
production stock flags сами по себе свежими не объявлялись.

## Новые отдельные helpers

- `scripts/catalog_refresh15_download.py` — ровно четыре fixed HTTPS export:
  BAON19982, Shopping Live25461, Serginnetti26417, Post Meridiem25200.
  Credentials `{website_id,user,code,run_id}` поступают одним JSON через stdin,
  не argv/env/file. Redirects и env proxy запрещены; стандартная TLS-проверка
  сохранена. Каждый decompressed body ограничен256MiB, HTTP read timeout120s,
  отдельный Linux ITIMER_REAL ограничивает полный запрос600s. Родительский
  процесс всё равно обязан ограничить весь контейнер и корректно остановить
  именно свой контейнер при timeout. Manifest содержит действительные моменты
  начала запросов, hashes и размеры; не URL/credentials. Нужна новая пустая
  writable mount `/feeds`. Неполная загрузка не даёт complete manifest.
- `scripts/catalog_refresh15_import.py` — импорт только новой UUID-копии
  актуальным build15 parser/normalizer после миграции0028. Ни один старый helper
  release14 не изменён и не исполняется. Нужен read-only mount `/feeds`.
  Все четыре CSV, pinned manifest/hash, часы, число строк и идентификаторов
  проверяются до первой записи. Сохраняется одна транзакция четырёх источников,
  прежние IDs, а также сравнение нецелевых products/catalog_records/sources.
  Отсутствующие SKU отключаются только у четырёх источников. Неизвестные размеры
  не превращаются в подтверждённые, цены идут через текущий Decimal pipeline.
- `scripts/test_catalog_refresh15_helpers.py` —10 offline проверок, все прошли.
  В частности, production URL и архив без закреплённого SHA отклоняются до
  импорта app/открытия соединения; старый snapshot нельзя обновить временем
  обработки; неполный набор и заменённые файлы не проходят. Это не PG/HTTP smoke.
- `scripts/catalog_refresh15_production_import.py` — отдельный строгий production
  worker, SHA256
  `b9bf37d9bb911f6354e7182922aa1d4acad78bc14f7d9f2f9632a4773ebc0688`.
  Его guards не отключались для проверки: создан отдельный PostgreSQL с новой
  internal-сетью, alias `postgres`, БД `modish` и ролью `modish_user`. Сеть не
  связана с production и портов не публикует. Синтетический plan/approval marker
  передавался только этому изолированному worker и не является разрешением
  изменять рабочую БД. Проверены все четыре snapshots до записи, единый commit,
  сохранение прежних ID/FK и всех не затронутых строк, а также durable outcome
  ledger без автоматического повтора при неопределённом commit.

Helpers передаются отдельно от неизменного архивного code package, со своими
SHA256; включение их в другой archive без новой фиксации/приёмки не подразумевается.

## Порядок для проверенного host-side драйвера

1. Повторить preflight: точные ID/image/revision рабочего backend/PG, здоровое
   состояние API, отсутствие нового обновления, свободное место. Доступные
   контейнеры prod сохраняются запущенными. Не читать raw Env/Cmd наружу.
2. Создать новую private UUID-папку под `/opt/modish-releases/`, mode0700.
   Не переиспользовать существующие outputs, каталог feeds или старые credentials.
   Проверить hashes архива и всех helpers до извлечения/исполнения. Сборка build15
   идёт из безопасно извлечённого архива; закрепить полученный immutable image ID.
3. Снять новый согласованный `pg_dump -Fc --no-owner --no-acl` production0025
   с readonly session и timeout. Dump хранится только в private каталоге0600,
   не печатается и не переносится в Git/пользовательский Downloads. Запросить
   только необходимые четыре export settings из копии после восстановления.
4. Проверить, что `modish_foundation_test_pg` — другой контейнер на единственной
   internal-сети `modish-foundation-test`, не публикует портов и не разделяет
   production network. Проверить его фактический локальный admin role отдельно.
5. `run_id=uuid4().hex`. Новая database строго `modish_refresh15_<run_id>`, новый
   login role строго `refresh15_<run_id>`. Проверить отсутствие обеих до создания.
   Роль без SUPERUSER/CREATEDB/CREATEROLE/REPLICATION, новый случайный пароль.
   Передавать SQL/credentials только stdin/защищённым private env, не argv/logs.
   У нового role нет доступа к prod PG даже на уровне сети.
6. Восстановить dump `--no-owner --no-privileges` с владельцем новой роли.
   Не менять прежние test databases. Проверить source revision0025 и identity.
   Одноразовый контейнер закреплённого build15 image, entrypoint `python`,
   internal network, private env, мигрирует **эту** БД в `20261015_0028`.
7. В памяти разобрать сохранённые четыре ProductSource.feed_url. Разрешены
   фиксированные origin/path/IDs Admitad, общий website/user/code, без redirects,
   URL userinfo/fragments/лишних query. Не печатать значения. Само изменение
   ADMITAD env не переписывает сохранённые feed_url; bootstrap всех магазинов
   ради четырёх не запускать.
8. Запустить downloader отдельным непривилегированным контейнером image ID,
   отдельная outbound network без prod/test PG, без DB/env production.
   CPU0.5, memory512MiB, pids64,
   cap-dropALL, no-new-privileges, read-only rootfs, новый feeds mount writable.
   Общий timeout контейнера не более2500s; внешний watchdog при ошибке сверяет
   ID/image/свои mounts перед stop. Manifest только после полного успеха.
9. Закрепить SHA256 complete manifest. Запустить importer отдельным контейнером
   CPU1, memory1536MiB, pids128, internal network, cap-dropALL/no-new-privileges,
   read-only rootfs и helper/feeds mounts. Фактический import timeout —7200s;
   SQL statements120s, lock timeout5s. Environment:

   ```text
   APP_ENV=test
   PYTHON_DOTENV_DISABLED=1
   PYTHONDONTWRITEBYTECODE=1
   PYTHONOPTIMIZE=0
   REFRESH_RUN_ID=<32hex>
   AUDIT_IMAGE_ID=<immutable sha256:image>
   AUDIT_ARCHIVE_SHA256=5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24
   REFRESH_MANIFEST_SHA256=<actual fresh manifest SHA256>
   DATABASE_URL=<private new role/password URL to fixed test host/database>
   ```

   Не передавать PG* connection overrides, fixed clock или production dotenv.
   Ошибка до commit откатывает транзакцию. Неопределённый результат commit
   требует отдельной проверки БД, а не автоматического повторения импорта.
10. Проверить реальные counts/hashes, source clocks, ID preservation, внецелевые
    rows, schema0028, readiness и реальные API journeys десяти test profiles,
    включая L/L/L EU38 с бюджетом100000. Проверить обувь/верхнюю одежду, категории,
    отсутствие повторов и честный stock/size gate. Не обещать полный образ,
    если нет подтверждённого размера/комплементарного SKU.
11. Сравнить baseline identity/health production после эксперимента. Не включать
    scheduler и не выполнять production import/activation этим драйвером.
    Сохранить dump/UUID clone для проверяемой приёмки; cleanup только для своих
    проверенных ресурсов отдельным действием. Показать результаты и конкретный
    production plan для следующего согласования.

Нужен самостоятельный review фактического host-side драйвера и всех команд.
Этот документ не исполняет шаги, не заменяет preflight и не даёт разрешения
на production import/deployment.

## Итоговая приёмка 22 октября 2026

Свежие CSV загружены 21 октября, с реальными временем начала запроса,
SHA256 и размером в pinned manifest
`5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c`.
Проверены BAON —9318 строк, Shopping Live —43402, Serginnetti —17065,
Post Meridiem —378: всего **70163 строки**, без повторяющихся source IDs.
Stock observation не заменялся временем обработки; граница свежести72 часа
проверяется перед записью и перед commit.

Первый импорт в UUID-копию прошёл одной транзакцией. QA на этой копии проверил
10 профилей, 270 discovery rows, реальные15 лайков и две порции персональной
ленты по10 карточек без повторов. Для L/L/L, EU38 и бюджета100000 отсутствие
подтверждённой обуви осталось честным ограничением: неподтверждённый размер
не был повышен до подходящего, полный wow-образ не выдумывался.

Дополнительный прогон неизменённого production importer:

- `exercise_id=e92cc56981d64f9a94dda5e2959f7ed1`.
- В отдельном новом PostgreSQL восстановлен закреплённый dump production0025.
  Counts и полные нормализованные row hashes **всех31 таблиц** совпали с
  исходной копией до и после миграции в `20261015_0028`.
- Production importer `b9bf37…` выполнил реальный атомарный импорт70163 строк
  четырёх источников. Прежние ID/FK и не затронутые строки сохранены.
- Отдельный PG ограничен CPU0.5/memory512MiB/pids128; importer —
  CPU1/memory1536MiB/pids128 и timeout7200s. Миграция выполняется отдельно,
  CPU0.75/memory768MiB, до запуска importer. Сеть `Internal=true`, портов нет;
  production containers к ней не присоединены. Preflight требовал не менее
  2300MiB MemAvailable и8GiB свободного диска.
- **35 guard-тестов реально выполнены на Linux:35 PASS,0 skips.**
- После разрыва SSH уже выполненный импорт не перезапускался. Результат
  подтверждён сохранёнными durable отчётами; собственный тестовый PG остановлен,
  данные и приватные артефакты сохранены для проверки.

SHA256 итогового exercise report:
`7600eb3224d4b8584cfe260552cd466c7695941f0b04d13003305593ea0684e2`.
SHA256 durable committed report:
`564c033dbbaa4d75450252b213ead4934fbe02dbc3dc5433c7d26559d092ef0f`.
Агрегированная локальная фиксация —
`.tmp/build15-isolated-final-acceptance-20261022.json`;
она содержит только результаты/идентификаторы/hashes, без dump или credentials.

Предыдущие отказы сохранены как отдельные попытки, без replay старого stage:

- Guard отклонил mode0644 у clone helper, требовавшего0600, до SQL. Права
  сужены после повторной проверки hash; прежняя запись об отказе сохранена.
- Следующая попытка создала только собственные сеть/PG, но PostgreSQL завершил
  init с permission denied: вложенный `PGDATA=/var/lib/postgresql/data/pgdata`
  оказался за root-owned bind parent0700. До создания/восстановления прикладной
  БД и SQL-импорта попытка остановилась; её артефакты сохранены.
- Минимальное исправление задаёт `PGDATA=/var/lib/postgresql/data`, ровно корень
  bind mount. Добавлены contract/negative tests; running/ownership/network,
  resource limits и права не ослаблялись, chmod777 не применялся. Успешный
  прогон использовал новый UUID и новые ресурсы.

Рабочий API и production БД остались на исходной версии. Следующий шаг —
отдельно согласованный production package с maintenance fence, финальным backup
и restore proof, контролируемой миграцией/import, smoke и планом восстановления.
Публичный выпуск приложения и его Android signing этим прогоном не подтверждены.
