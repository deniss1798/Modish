# Подготовка нового обновления v20 — 9 октября 2026

Сценарий подготовлен локально. SSH, новые downloads и импорт не выполнялись. Main agent должен сначала проверить эти новые файлы и дождаться успешного `stage-report.json` точного v20.

## Область действия

Разрешены только BAON (`19982`), Shopping Live (`25461`), Serginnetti (`26417`) и Post Meridiem (`25200`). Конфигурация берётся read-only из сохранённых четырёх `ProductSource.feed_url` в точной изолированной БД. Проверяются origin/path/feed ID/формат и одинаковые website/user/code; затем URLs строятся заново для фиксированного HTTPS endpoint. Произвольные URLs БД не передаются загрузчику.

Credentials проходят только через subprocess pipe и память. Загрузчик не импортирует app/DB, не получает production `.env`, не читает production БД. Передача конфигурации — stdin, а не аргументы/environment/logs. Redirects и environment proxy запрещены; TLS проверяется. Максимум 256 MiB на источник и 10 минут чтения одного ответа. Снимки публикуются эксклюзивно после полного получения; complete manifest появляется лишь после всех четырёх.

Новые данные и логи создаются только в `/opt/modish-releases/20261009-intent-v20/refresh`. Уже существующий `refresh` запрещает повторный запуск. Предыдущие каталоги/архивы/отчёты не перезаписываются.

## Порядок выполнения

1. Проверить завершённый v20 stage, immutable image/archive/helper hashes, исходную production container identity и точную internal test network/PG/user/database/schema.
2. Скачать четыре exports в новый каталог, сохранив SHA256/bytes и реальное UTC-время начала запроса каждого источника. Проверить, что сохранённый каталог БД не изменился от загрузки.
3. Сделать `pg_dump --format=custom` только `modish_foundation_test_pg/modish_test`, проверить backup TOC и hash, убедиться в отсутствии изменений каталога до первого import.
4. До любых writes проверить complete manifest всех источников, hashes, реальные timestamps (не старше четырёх часов), непустой strict semicolon CSV с ожидаемыми полями, корректность рядов и число строк/уникальных идентифицируемых ID. Падение числа строк или distinct IDs более чем вдвое относительно снимка 6 октября/текущих retained records останавливает сценарий для review. Идентичные повторы считаются отдельно, конфликтующие повторы одного ID отклоняются. Нельзя автоматически трактовать подозрительный partial export как исчезновение каталога.
5. Импортировать все четыре источника одной транзакцией через существующий `store_record`. `last_seen_at` берётся из download observation, processing timestamp остаётся отдельным. Raw fingerprint сохраняет существующий SHA256 от `json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()`; новый run timestamp не добавляется в raw. Существующие Product IDs сохраняются. Missing marks ограничены четырьмя источниками и используют один ARRAY bind для больших допустимых feeds. Глобального stale disable и обновления времени при renormalize нет. При ошибке/timeout отдельный sanitized import outcome отражает confirmed commit, confirmed rollback либо неизвестность результата commit; failure runner сам по себе не означает rollback.
6. Запустить read-only raw size inventory и current-clock readiness десяти контролируемых профилей на реальном каталоге. Реальные 15 Style Swipes (10 likes / 5 dislikes) проходят обычную completion-логику. `completed_at` не присваивается вручную; insufficient activation отдельно отмечается. Gate требует завершения activation, трёх WOW, ≥10 feed cards, shoes/outerwear, office и wedding/no-dress/no-heel coverage. Synthetic users/tasks полностью откатываются.
7. Проверить неизменность каталога после audit, identities production containers и сохранённые состояния остальных источников изолированной БД. `product_ready` копируется только из новых десяти профилей; технический success импорта не закрывает coverage gate.

## Новые файлы и интерфейс

| Локальный helper | Новое имя на release host |
|---|---|
| `scripts/intent_v20_refresh_remote.py` | `refresh-runner.py` |
| `scripts/intent_v20_refresh_download.py` | `refresh-download.py` |
| `scripts/intent_v20_refresh_import.py` | `refresh-import.py` |
| `scripts/intent_v20_refresh_readiness.py` | `refresh-readiness.py` |
| `scripts/audit_merchant_size_fields_v20.py` | существующий проверенный `size-raw-inventory.py` v20 |

Runner принимает `archive_sha256 image_id --download-sha256 ... --import-sha256 ... --readiness-sha256 ... --inventory-sha256 ...`. Все hashes обязательны. Helpers downloads/import/readiness не принимают аргументов; controller задаёт безопасные audit IDs и exact test environment. Downloader получает только четыре поля конфигурации через stdin. Root filesystem helper containers read-only; feed mount writable только загрузчику.

Данные и отчёты: `refresh/feeds/*.csv`, `refresh/feeds/download-manifest.json`, `refresh/test-before-refresh.dump`, `refresh/before-import.json`, четыре `*-report.json`, соответствующие `*-errors.log`, финальный `refresh-report.json` либо `refresh-failure.json`. Backup содержит изолированную копию чувствительных данных и остаётся в защищённом каталоге host; в отчёте только имя/размер/hash. Автоматическое восстановление/production deployment не выполняется.

## Проверки и ограничения

Синтаксис всех четырёх helpers проверен. Тринадцать offline prepare tests прошли: fixed export guards, запрет redirect/oversize, отсутствие credentials в сохранённых artefacts, полный manifest/hash/path/source-set guards, refusal старого observation и production/query override перед DB import, distinct-ID/duplicate guards, ARRAY bind на 100 000 IDs и сохранение uncertain commit outcome. HTTP и DB в этих тестах не вызываются. Linux hard-link publication primitive моделируется в Windows-тесте, поскольку local sandbox запрещает hard links. Readiness helper дополнительно прошёл 33 безопасные проверки guard/clock/gate/parser логики.

Без remote execution пока не доказаны возможность получения новых четырёх exports, свежий размерный pool, успешность нового реального импорта и product readiness. Production guard сравнивает container identity/running state; scripts не подключаются к production БД для записи. Full-row fingerprints внутри PostgreSQL используются для обнаружения случайных изменений, а не как криптографическое доказательство независимого состояния поставщика.
