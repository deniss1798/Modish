# Build 15: HTTPS-конфигурация публичного приложения

Release-клиент проверяет базовый URL до создания собственного Dio transport и до добавления auth/session interceptors. Проверка обязательна при `kReleaseMode`, в том числе для переданного извне Dio. Параметр `enforceReleaseConfiguration` позволяет включить ту же проверку в локальных тестах; выключить её в настоящем release этим параметром нельзя.

`MODISH_API_BASE_URL` имеет приоритет над `productionApiBaseUrl`. Для release после выбора адреса требуется абсолютный HTTPS URL; пустая или непригодная конфигурация не заменяется адресом эмулятора. Текущий production default — `https://modish.org.ru`.

Отклоняются:

- HTTP, относительные и другие не-HTTPS адреса;
- userinfo, включая пустой `@`, query и fragment;
- пустой/некорректный host, пробелы, control characters, обратные слэши, недопустимые DNS labels и неправильные порты;
- localhost, одночастные локальные имена, `.local`/`.internal`, обычные private/loopback/link-local IPv4 и IPv6, mapped IPv4 literals.

Валидный нестандартный HTTPS порт и префикс пути API допустимы. Внешние пробелы и завершающие слэши нормализуются. Debug/profile сохраняют существующую конфигурацию локального HTTP API, включая `10.0.2.2:8773` для Android-эмулятора. Ограничение Android cleartext отдельно задаётся network security resource соответствующего build type.

Это локальная структурная проверка доверенной build-конфигурации. Она не выполняет DNS/HTTP, не удостоверяет доступность сервера или сертификат и не доказывает, что DNS-имя разрешается в публичную сеть. Классификатор литералов адресов не является полной сетевой политикой или защитой от произвольного недоверенного кода. Корректный production endpoint и его HTTPS всё равно проверяются перед публикацией.

## Поведение при ошибке

Конфигурация отклоняется как `ApiConfigurationException` с единственным фиксированным кодом `MODISH_RELEASE_API_CONFIGURATION_INVALID`. Исходный URL и потенциальные credentials не включаются в сообщение.

`ModishBootstrap` перехватывает только эту ошибку при создании контроллера. Вместо пустого экрана/stack trace отображаются «Не удалось запустить приложение» и инструкция установить актуальную версию Modish из официального источника. Auth-поля и бесконечный retry отсутствуют: повторный сетевой запрос не исправляет неправильно собранный адрес. Остальные ошибки bootstrap этим обработчиком не маскируются.

## Проверка 20 октября 2026 года

Команда:

```powershell
& C:/src/flutter/bin/flutter.bat test `
  test/api_release_configuration_test.dart `
  test/style_visual_capability_test.dart `
  test/widget_test.dart --reporter expanded
```

**59 тестов успешно.** URL-набор проверяет нормальные HTTPS endpoints, IPv4/IPv6, неправильные схемы/порты/hosts, встроенные credentials и локальные адреса. Отдельные transport-тесты доказывают отказ до health/auth dispatch, сохранение HTTPS health/login и debug HTTP. Widget-тест проверяет безопасный стартовый экран без утечки URL, auth-полей, повторных попыток и framework exception. В тот же прогон вошли два теста visual capability и обычный bootstrap smoke. Все HTTP-вызовы перехвачены локальными fake adapters; рабочий сервер не использовался.

Лог: `.tmp/build15-public-api-focused.log`. Эта проверка не является сборкой, подписью или публикацией пользовательского APK.
