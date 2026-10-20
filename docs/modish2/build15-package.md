# Build15: неизменяемый пакет и локальная проверка

Сборщик и проверка работают локально. Они не подключаются к VPS, не изменяют
рабочую БД, не получают фиды и не меняют даты наличия. Наличие этих сценариев
не означает, что build15 опубликован или проверен на копии рабочей БД.

## Что фиксирует пакет

`scripts/package_release15.py` принимает полный Git commit ID из 40 строчных
hex-символов. Список файлов, байты, режимы и время берутся из объектов этого
коммита через `git ls-tree`/`git cat-file`, а не из текущего рабочего дерева.
Git replacement objects отключены; сетевые протоколы и lazy fetching запрещены.
Содержимое приложения не переписывается и не накладывается на старый архив.

Включаются все допустимые tracked-файлы `backend/`. Исключаются секретные env
файлы, virtualenv, generated/cache/build-каталоги, ключи, базы и бинарные
артефакты. Безопасный `.env.example` остаётся обычным tracked текстом.
Исключения перечислены в manifest. Неизвестный бинарный контент вызывает отказ,
а не молчаливое исключение. Текст дополнительно проверяет credential guard;
значения предполагаемых секретов не выводятся.

Для полного backend-набора нужен layout с соседними файлами. Точный дополнительный
allowlist:

- `scripts/check_backend.py`;
- `scripts/benchmark_activation_feedback.py`;
- `scripts/scan_secrets.py`;
- `scripts/run_device_fixture.py`;
- `test/fixtures/build12-blouse.jpg` — единственное бинарное исключение.

`source-manifest.json` содержит SHA256 каждого файла, Git commit/tree, режимы,
API version, исходную/целевую миграции, ожидаемое число тестов и число PG test
methods. Базовый image закреплён на сохранённом build14:
`sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec`.
Проверка удалённого фактического image перед выпуском остаётся отдельным шагом.

AST-проверки требуют `API_VERSION = '1.0.1-build15'`, единственный migration head
`20261015_0028` и цепочку 0025 → 0026 → 0027 → 0028. Дубли revision, отсутствующие
родители, циклы и дополнительные heads отклоняются. Код metadata не импортируется.

Архив создаётся исключительно новым файлом. Повторное имя вызывает отказ;
частичный результат ошибки также не перезаписывается. При одинаковом коммите
и параметрах архив воспроизводим: порядок, mtime, uid/gid и режимы фиксированы.

## Запуск

Сначала коммит должен уже содержать согласованную версию приложения. Здесь
`COMMIT_40_HEX` и `ARCHIVE_SHA256` — обозначения, которые нужно заменить реальными
идентификаторами; сокращённый SHA или `HEAD` сборщик не принимает.
`--expected-tests` обязателен: число фиксируется для выбранного исходного
среза, а устаревшее значение по умолчанию не применяется.

```powershell
& backend/.venv/Scripts/python.exe scripts/package_release15.py `
  --commit COMMIT_40_HEX `
  --output .tmp/modish-build15-COMMIT_40_HEX.tar `
  --expected-tests 944

& backend/.venv/Scripts/python.exe scripts/check_release15_package.py `
  --archive .tmp/modish-build15-COMMIT_40_HEX.tar `
  --sha256 ARCHIVE_SHA256 `
  --commit COMMIT_40_HEX
```

CLI сборщика разрешает output только в workspace `.tmp`. Checker сначала целиком
проверяет внешний SHA256, manifest, regular-file типы, allowlist, hashes, режимы,
дубли имён/JSON keys, пути и лимиты. Traversal, absolute/backslash/Windows-device
пути, symlink/hardlink/directory entries и case collisions не допускаются.
Лимиты: архив/общие данные 256 MiB, файл 32 MiB, manifest 4 MiB, payload 10000 файлов.
До завершения проверки архивный Python не исполняется и файлы не извлекаются.

После preflight создаётся новая `.tmp/release15-check-<UUID>`. В ней остаются
точные исходники, manifest, `backend-tests.log`, `test-result.json` и
`package-check-report.json`. Существующие каталоги не переиспользуются.

Checker запускает отдельный Python с очищенным окружением, synthetic SQLite/JWT,
собственным TEMP и отключённым dotenv. До app imports устанавливается socket/DNS
guard, разрешающий только loopback. Runner `check_backend.py` и guard из
`run_device_fixture.py` импортируются из проверенной копии, а не из рабочего
дерева. Свежие дочерние процессы device fixture устанавливают собственный guard.
Это ограничение обычных Python-сокетов; оно не является отдельной OS-песочницей
для исполнения произвольного недоверенного кода. Выпуск использует проверенный
командой Git-коммит и внешний доверенный SHA256 архива.

## Как читать результат

Для текущего набора после публичного audit ожидаются 944 обнаруженных теста:
889 выполняются на SQLite,
55 PG-сценариев пропускается. `postgres_test_methods` определяется AST из
зафиксированного источника; checker дополнительно требует фактического совпадения
чисел и точной причины каждого пропуска `Opt-in disposable PostgreSQL fixture`
в PG-модуле. Другие пропуски, потеря тестов или зелёный неполный набор не проходят.
Пропущенные PG-сценарии не записываются как выполненные. Новые тесты самих
release scripts запускаются отдельно, в 944 backend tests не входят.

```powershell
& backend/.venv/Scripts/python.exe -m unittest discover `
  -s scripts -p test_release15_package.py -v
```

Локальная проверка пакета не заменяет реальный upgrade 0025 → 0028 на новой копии
рабочего PostgreSQL, проверку совместимости отката, freshness/coverage каталога
и удалённую приёмку. Миграции 0026/0027 меняют тип цены и nullable gender, поэтому
возврат старого API и schema downgrade требуют отдельного плана. Checker не
выполняет ни эти операции, ни публикацию APK.
