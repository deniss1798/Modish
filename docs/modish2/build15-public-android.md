# Build15: Android-конфигурация публичного выпуска

20 октября 2026. Изменения конфигурации, без создания production-ключа,
публичного APK, обновления сервера или очистки данных приложения.

## HTTPS и варианты сборки

Основной `res/xml/network_security_config.xml` явно запрещает cleartext.
Release наследует этот ресурс без локальных исключений. Ресурсы debug и
profile разрешают HTTP только к `10.0.2.2`, `127.0.0.1` и `localhost` для
изолированного стенда. Прежнее HTTP-исключение публичного IP удалено.
Manifest по-прежнему содержит `usesCleartextTraffic=false`.

Это Android network policy; проверка release URL в Dart дополняет её,
а не заменяется ею. Базовый API остаётся `https://modish.org.ru`.
Основание: [Android Network Security Configuration](https://developer.android.com/privacy-and-security/security-config).

## Backup и перенос между Android-устройствами

Manifest задаёт `allowBackup=false` и две версии правил. Legacy
`fullBackupContent` используется до Android12; `dataExtractionRules` —
на API31+. В последних отдельно описаны cloud backup и device transfer:
на части устройств одного `allowBackup` недостаточно для D2D.
Во всех секциях исключены все девять документированных storage domains,
включая shared preferences и device-protected storage. Имена отдельных
prefs не зафиксированы, поэтому правила охватывают и key material плагина.

TokenStorage и FeedHistory используют encrypted preferences пакета9.2.4;
его [документация](https://pub.dev/packages/flutter_secure_storage/versions/9.2.4)
предупреждает о восстановлении шифротекста без ключа исходного устройства.
Правила соответствуют [Android Auto Backup](https://developer.android.com/identity/data/autobackup).
`resetOnError` не менялся; автоматическое удаление хранилища не добавлялось.
Ранее созданные копии и уже повреждённое restore-хранилище этим изменением
не удаляются и не исправляются. Реальный OEM backup/restore не выполнялся.

## Проверка фактических Android-ресурсов

Используется настоящий cached Gradle manifest merger/AAPT2. Проверяются
производные manifest/resources, а не unit-тест, повторяющий строки исходного XML.
Команды выполняются без APK, release-подписи и изменения signing gate:

```powershell
& android/gradlew.bat -p android :app:processDebugMainManifest :app:mergeDebugResources --offline --no-daemon --no-configuration-cache --console=plain
& android/gradlew.bat -p android :app:processDebugResources --offline --no-daemon --no-configuration-cache --console=plain
```

Manifest/resource merge прошёл: **20 с**, 51 tasks, 4 executed/47 up-to-date.
Лог — `.tmp/build15-public-android-processing.log`. В граф входил cached
`compileFlutterBuildDebug`, но packaging/signing APK не запускались.

Полный AAPT2 resource link также прошёл: **16 с**, 112 tasks,
11 executed/101 up-to-date. Лог — `.tmp/build15-public-android-linked-resources.log`.
В фактическом merged manifest подтверждены версия `1.0.1+15`, minSdk24,
targetSdk36, `allowBackup=false`, обе ссылки backup и network resource.
`aapt2 dump xmltree` из полученного `.ap_` подтвердил debug allowlist ровно
из трёх loopback-адресов, девять legacy исключений и по девять исключений
в cloud/device-transfer. Их domains и `path="."` сверены с контрактом,
случайных `include` в backup нет. Сводка —
`.tmp/build15-public-android-resource-inspection.json`, decoded dump —
`.tmp/build15-public-android-compiled-resources.txt`.

Release processing и полный публичный APK пока не принимаются: без внешней
подписи release-задачи отклоняет обязательный signing gate. Эта граница не
обходилась dummy-keystore или временным debug signing. Эти локальные проверки
не подтверждают готовность конкретного публичного артефакта. Profile overlay
и release source set проверены по исходникам, без отдельной variant-сборки.
