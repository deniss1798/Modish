# Ревизия клиентской архитектуры и жизненного цикла сессии

13 октября 2026. Проверка исходников Flutter и локальных сценариев с fake API. Рабочий сервер, backend, каталог и APK в этом блоке не изменялись.

## Исправленные дефекты

| Приоритет | Дефект до исправления | Изменение | Проверка |
| --- | --- | --- | --- |
| P1 | После выхода незавершённые фоновые чтения возвращали данные прежнего аккаунта в профиль, сохранённое и образы. | Одна граница сброса account-owned state и поколение сессии. Все фоновые коммиты проверяют текущую сессию; выход очищает также onboarding, фото, fit/taste, billing и shopping task. | Старые summary/fit/saved ответы после logout не заменяют данные следующего аккаунта. |
| P1 | Запоздалый login/register/session-upgrade мог восстановить токен после logout; успешный ответ защищённого запроса другой сессии доходил до экранов. | API отклоняет устаревшие защищённые ответы и принятие auth-токенов. Старый 401 не очищает новую сессию. | Deferred Dio adapter: logout в середине auth, old-account success/401, session upgrade. |
| P1 | Автоматический GET retry мог повторно отправить старый bearer после смены сессии. | Проверка поколения перед повтором после задержки. | После logout транспорт получает один запрос, повтор отменён. |
| P1 | Исключение разбора malformed refresh вне DioException оставляло исходный interceptor без завершения. | Любая ошибка refresh завершает исходный запрос; временная ошибка не трактуется как подтверждённый 401. | Пустой refresh JSON возвращает ошибку и сохраняет сессию. |
| P2 | Старый refresh удерживал общий Future и мог мешать обновлению следующей сессии. | Future refresh принадлежит поколению; очистка старого Future не очищает новый. | Два независимых refresh; старый ответ не устанавливает токен. |
| P2 | Ошибка чтения стирала последние подтверждённые коллекции; каждое фоновое обновление выполняло восемь последовательных запросов. | Независимые чтения идут параллельно, повторные вызовы объединяются; неуспешное чтение сохраняет последнее подтверждённое значение. | Ошибки не очищают fit/taste/saved/outfits; два concurrent refresh разделяют один набор чтений. Реальную production-латентность отдельно не измеряли. |
| P2 | Первый из двух `_run`-actions выключал общий loading до завершения второго. | Loading учитывает набор незавершённых операций текущей сессии. | Один завершённый mutation не снимает busy второго. |
| P2 | Запись нового access token и удаление secure storage при logout могли завершиться в обратном порядке. | Запись и удаление токенов сериализованы; callback перед сохранением проверяет поколение. | Fake secure storage: write блокируется, logout ждёт и выполняет delete после write. |
| P2 | AppController после dispose принимал поздние результаты/уведомления и не закрывал созданный HTTP transport. | Dispose инвалидирует поколения, снимает auth callbacks и закрывает только собственный ApiClient. Injected client сохраняет ownership вызывающего кода. | Поздняя лента после dispose не применяется; late API response отменён; injected client не закрывается. |
| P1 | После успешной авторизации reset поколения подавлял ошибки дальнейшей маршрутизации и слишком рано снимал loading. | Routing выполняется отдельной операцией нового поколения; fit confirmation остаётся внутри текущего аккаунта и сохраняет навигационную идентичность. | Login/register → ошибка onboarding read показывают ошибку текущей сессии; fit confirmation не очищает текущий профиль и epoch. |
| P2 | Прежний контроллер общего injected ApiClient при dispose снимал callbacks нового владельца. | Callback detach проверяет identity конкретного обработчика. | Два контроллера одного injected API: dispose первого сохраняет unauthorized обработчик второго. |
| P1 | Завершение внешнего перехода после смены аккаунта могло записать старый affiliate context с новыми токенами. | Merchant helper сохраняет read-only API session epoch; поздний успешный launch остаётся успешным, но его старый контекст не отправляется новому аккаунту. | Fake external-launch Completer + смена сессии: API confirmation не отправлен. |
| P1 | При немедленном logout до async interceptor dispatch прежний bearer получал stamp уже нового поколения, и его успешный ответ мог пройти guard. | BaseOptions.extra синхронно сохраняет поколение при создании RequestOptions; все смены identity обновляют его. Stale onRequest отклоняется до транспорта. | Сначала отдельный failing fake-Dio probe вернул email прежнего аккаунта. После исправления formal GET/POST tests завершаются cancel, транспорт не вызывается. |

Первый блок изменял AppController в `lib/app.dart` и session/transport lifecycle в `lib/core/network/api_client.dart`; новые тесты — `test/app_session_lifecycle_test.dart`, `test/api_session_lifecycle_test.dart`. После root-integrated nav/theme ревизия дополнительно закрыла attribution в `merchant_link.dart`, brand/search/detail interactions и presentation mapping; image widget получил optional thumbnail retry policy. Backend, API-контракты товара и eligibility не менялись этим агентом.

## Навигация как граница аккаунта

Обнаруженный P1 навигации исправлен в root UI: смена `RootScreen.stage` сама по себе не закрывала pushed Detail/Search/EditProfile routes прежней сессии. `ModishApp` подписан на контроллер и использует `navigationSessionEpoch` в ключе MaterialApp. Epoch меняется при входе/выходе/истечении сессии и сохраняется при access-token rotation и подтверждении fit внутри того же аккаунта. Проверки root в `test/navigation_session_test.dart` должны оставлять pushed route при token rotation и удалять его при logout/unauthorized; результат общего прогона фиксируется владельцем release.

`AppTheme.dark` вынесена в отдельный design-system файл и хранится как `static final ThemeData`: повторные уведомления контроллера не создают новую тему. Keyed MaterialApp пересоздаёт Navigator только на границе аккаунта; обычное изменение ленты, фильтров и loading сохраняет ключ.

## Реальные границы экранов

Основной путь: Welcome → Auth → Fit confirmation при необходимости / Onboarding V2 → Home. Home сохраняет состояние четырёх вкладок в IndexedStack: Feed, Outfits, Saved, Profile. Из них доступны Search, Filters, Product Detail, Outfit Detail, Shopping Task при API capability, Edit Profile, Brands, Settings/Help, дополнительный Upload/Analysis и результаты анализа.

`RecommendationsScreen`, `CollectionsScreen`, `PlusScreen` не имеют навигационных точек входа в текущем основном пути. Старые Intro/FitQuiz ветки остались в RootScreen, но текущий Welcome ведёт сразу в Auth. Не следует добавлять их обратно ради покрытия или объявлять их частью нового пользовательского пути. Удалять старые ветки следует отдельной миграцией после проверки сохранённых ссылок и тестовых fixtures.

Search уже использует номер запроса и debounce для изменения строки. ShoppingTaskController уже отделяет task state от Taste и защищает результат поколением запроса; его optimistic-conflict refresh повторно читает сервер, не повторяет mutating refinement. Это полезные существующие границы, которые следует сохранять.

## Сопровождаемость

AppController всё ещё объединяет auth, activation, feed, saved и fit orchestration. Первое необходимое разделение здесь — граница аккаунта и lifecycle, а не произвольное разрезание файла. Теперь повторный сброс собран в одном месте; повторный outfit reload общий, secondary reads имеют общий admission к состоянию. ThemeData и navigator configuration можно вынести отдельно без изменения бизнес-поведения.

Следующий обоснованный шаг — типизированный snapshot/repository для account reads и feature-specific controllers при появлении независимых экранных обновлений. Переход следует делать по одной границе с сохранением текущих HTTP contracts и проверок stale responses. Массовая замена Map-моделей или introduction state-management framework без отдельного сценария и тестов сейчас увеличит риск.

Eligibility, подтверждение размерной системы, stock freshness и допустимость комплекта остаются на backend. Клиент отображает серверные доказательства, не создаёт собственную конверсию размеров и не достраивает отсутствующий комплект случайными товарами. Архитектурная ревизия не решает недостаток размерных данных каталога.

## Проверки

Новые lifecycle сценарии и прежние refresh/merchant сценарии проверены fake Dio adapter / fake API / fake secure storage, без реального HTTP: 31 тест прошёл после routing/affiliate/callback fixes (`.tmp/frontend-session-routing-final-20261013.log`). Targeted `analyze --no-pub` шести файлов — без замечаний (`.tmp/frontend-session-analyze-final-20261013.log`).

Независимый просмотр после интеграции темы подтвердил проверки epoch перед async milestone/visual Navigator push. UI-агент также добавил mounted guard для закрытия shopping task: уход со страницы во время close не обращается к disposed TextEditingController; позднее сохранение не начинает refresh от уже закрытого экрана. Его targeted сценарии проверены отдельно.

Два P2 reachable interactions закрыты целевыми исправлениями. Profile принимает выбранный BrandsScreen бренд и открывает SearchScreen с автоматически выполненным initial query; второго ввода не требуется. Related strip в ProductDetail открывает выбранный ProductDetail и записывает `open_product` с `placement=complement` и исходным `reference_product_id`, без неверного копирования intent ownership на товар вне выданного task result.

Тест related tap обнаружил отдельную коллизию: кнопка retry внутри маленького photo placeholder забирала центральный tap карточки. У ProductFillImage появился optional `showRetry`, по умолчанию true; actionable related thumbnails используют false и открывают detail, где retry полного фото сохранён. Переход проверен и для failed/empty thumbnail, и для декодированного локального JPEG через in-memory HttpClient без сокетов.

Подписи цветов в Profile/Detail используют общую presentation mapping: известные `navy`/`beige` отображаются понятными названиями, неизвестное значение магазина сохраняется буквально. DTO и запросы не конвертируют merchant values. Network error copy больше не содержит backend/emulator/сервер URL; HTTP 5xx не раскрывает диагностический detail, валидация 422 сохраняет полезное сообщение, 403 не изображает истечение сессии.

Дополнительные проверки после этих изменений: 18 API request/refresh сценариев прошли (`.tmp/frontend-api-dispatch-final-20261013.log`); 54 catalogue/error/search/detail сценария прошли до добавления JPEG-проверки (`.tmp/catalog-interactions-functional-20261013.log`), затем 4 interaction сценария, включая JPEG, прошли (`.tmp/catalog-interactions-images-20261013.log`). В этих числах есть пересечения; складывать их как число уникальных тестов нельзя. Итоговый общий functional прогон выполняет root после freeze UI.

Финальный targeted `analyze --no-pub` всех 14 owned source/test/helper файлов — без замечаний (`.tmp/frontend-engineering-final-analyze-20261013.log`). После этой проверки source передан root и UI audit агенту как стабилизированный для общего functional запуска и последнего offline capture.

Исторический общий запуск `.tmp/frontend-session-functional-final-20261013.log` прошёл 143 сценария и выявил один UI-finder regression в `intent_outfit_sizes_test.dart`; владелец UI исправил finder через стабильный shoe-detail key, сохранив EU 38 и открытие detail. Его отдельный targeted прогон 32 сценариев прошёл. Владельцу root переданы завершённые lifecycle файлы для итогового общего анализа/functional прогона и проверки nav key; новые preview/audit harness в функциональное число не включаются.
