# Build15: внешняя release-подпись и совместимость альфы

20 октября 2026. Production-ключ не создавался, production release не
подписывался. Debug APK проверен отдельной сборкой, описанной ниже. Выбор
канала распространения не подменяется решением о подписи. Google Play в этом
срезе не предполагается.

## Release не использует debug по умолчанию

`android/app/build.gradle.kts` требует одну полную внешнюю конфигурацию:

| Поле `android/key.properties` | Переменная окружения |
| --- | --- |
| `storeFile` | `MODISH_SIGNING_STORE_FILE` |
| `storePassword` | `MODISH_SIGNING_STORE_PASSWORD` |
| `keyAlias` | `MODISH_SIGNING_KEY_ALIAS` |
| `keyPassword` | `MODISH_SIGNING_KEY_PASSWORD` |

Если задана хотя бы одна из этих переменных, используются только переменные:
частичный env не дополняется из файла. Если ни одна не задана, читается
локальный `android/key.properties`. Сам файл и файлы ключей исключены из Git.
Шаблон `android/key.properties.example` содержит только `REPLACE_ME`.
Пароли передаются без удаления пробелов; путь и alias очищаются по краям.
Относительный `storeFile` считается от каталога `android/`; на Windows
используйте прямые слэши или экранирование Java Properties.

Отсутствие конфигурации даёт `MODISH_RELEASE_SIGNING_REQUIRED`, неполные,
пустые поля, незаменённые placeholders, нечитаемый конфиг или отсутствующий
файл ключа — `MODISH_RELEASE_SIGNING_INVALID`. Эти сообщения не выводят
значения, пароли или частные пути. Ошибка возникает только для release-задач,
включая их вызов через aggregate task и `--dry-run`. Дополнительно доступна
задача `:app:validateProductionSigning`; сама она APK не собирает.

Проверка подтверждает полноту настроек и наличие читаемого файла, а не
правильность пароля/ключа или назначение сертификата. Криптографическую подпись
проверяют инструменты Android при сборке и `apksigner verify --print-certs`
на готовом APK. Общие логи Gradle/Android с приватным signing config следует
сохранять приватно: сообщения стороннего инструмента не проходят наш formatter.
Не передавайте пароли в аргументах команд и не публикуйте `signingReport`.

Скрипт не добавляет flavor, новый applicationId, временную release-подпись,
автоматическое создание ключа или флаг обхода с debug-подписью. Обычные debug
сборки остаются доступны при отсутствующей или неполной release-конфигурации.
Gradle task-graph guard рассчитан на обычную сборку без configuration cache;
контрольные команды ниже явно отключают его.

## Уже установленный build14

Публичные метаданные двух имеющихся APK проверены `apksigner` без чтения
keystore. Build14 `app-release.apk` и текущий `app-debug.apk` подписаны одним
Android Debug сертификатом SHA-256:

`56aa2a86188fb19e48f6dfbbb67bd59c4bd7aa67783b97edc8217a0bb6cc4f0d`.

Обычное обновление требует совместимой подписи. Новый неподготовленный ключ
не устанавливается поверх build14; rotation/lineage здесь не настраивались.
Переустановка может удалить локальные данные приложения. Серверный аккаунт
от смены Android-подписи не исчезает. Основание —
[официальная документация Android о подписи и обновлениях](https://developer.android.com/studio/publish/app-signing).

Для продолжающейся закрытой альфы возможен отдельно обозначенный debug APK
с прежним applicationId и сертификатом, проверенным по готовому артефакту.
Это сохраняет текущий installer-путь без неявного выпуска debug под видом
production release. Выдача такого APK и переход на новый постоянный ключ —
отдельные действия; наличие локальной debug-сборки не означает её публикацию.

## Проверки без ключа и APK

Негативный helper запускает настоящий Gradle `assembleRelease --dry-run`
для отсутствующей конфигурации, неполного env и отсутствующего файла ключа.
Затем `assembleDebug --dry-run` проверяет, что неполный release env не мешает
debug. Используются только синтетические значения. Скрипт отказывается
работать, если есть локальный `android/key.properties`, и не открывает его.
Вывод содержит только результаты, длительность и проверку сокрытия значений;
полные Gradle-логи не печатаются. Нужны уже закешированные Android/Flutter
зависимости и настроенный JDK; сеть отключена параметром `--offline`.

```powershell
& backend/.venv/Scripts/python.exe scripts/check_android_release_signing.py
```

По отдельности без release-конфига:

```powershell
& android/gradlew.bat -p android :app:validateProductionSigning --offline --no-configuration-cache
& android/gradlew.bat -p android :app:assembleRelease --dry-run --offline --no-configuration-cache
& android/gradlew.bat -p android :app:assembleDebug --dry-run --offline --no-configuration-cache
```

Первые две команды должны завершаться отказом с redacted marker, третья —
успехом. Положительная release-сборка требует отдельно предоставленной внешней
подписи и проверки сертификата; здесь она не объявляется пройденной.

## Фактическая приёмка 20 октября

Root выполнил helper против настоящего Gradle: **4/4** проверки прошли.

| Проверка | Результат | Время |
| --- | --- | --- |
| Release без конфигурации | Отказ `MODISH_RELEASE_SIGNING_REQUIRED` | 16,761 с |
| Release с неполным env | Отказ `MODISH_RELEASE_SIGNING_INVALID` | 10,125 с |
| Release с отсутствующим файлом ключа | Отказ `MODISH_RELEASE_SIGNING_INVALID` | 9,574 с |
| Debug с неполным release env | Успешный dry-run | 9,533 с |

Во всех четырёх случаях `synthetic_values_redacted=true`: подставленные пути,
alias и значения паролей отсутствуют в выводе Gradle. Helper не создал APK
или keystore. Локальный журнал — `.tmp/build15-signing-gate-20261020.log`.

Затем root отдельно собрал настоящий `app-debug.apk`: `assembleDebug`
завершился успешно за **80,2 с**. Журнал —
`.tmp/build15-alpha-debug-build-20261020.log`. Текущая версия исходников —
`1.0.1+15`. Независимый `apksigner verify --print-certs` на новом debug APK
успешен; SHA-256 сертификата совпадает с указанным выше build14. Для этой
проверки прочитаны только публичные метаданные APK, не keystore или его пароль.

Новая production-подпись, установка обновления поверх build14 и публичная
публикация этим набором проверок не выполнялись. Совпадение сертификата
подтверждает необходимое условие прежнего пути обновления, но не заменяет
проверку установки и восстановления сессии на выбранном устройстве.
