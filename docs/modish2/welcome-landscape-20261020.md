# Welcome: landscape и доступные действия

## Причина и изменение

`assets/images/back.png` уже содержит знак, MODISH и английскую подпись. `BoxFit.cover` с центральным выравниванием в landscape увеличивал эту портретную композицию, обрезал знак и помещал кнопки поверх букв.

Welcome теперь использует отдельную широкую композицию: декоративная верхняя часть того же фона, слева существующий знак/wordmark и `YOUR STYLE / SMARTER`, справа компактный блок входа и регистрации шириной 220–320 px. На узкой landscape-области блоки идут друг за другом. Исходные изображения сохранены, новые не создавались. Ориентация устройства не блокируется.

В landscape чистый `logo.png` накладывается без заметной прямоугольной matte: локальная ColorFilter matrix сохраняет RGB и переводит near-black фон в alpha. Светлое золото остаётся непрозрачным. Это только композиция Welcome; исходный файл и глобальный `Brand` не менялись. PNG после изменения просмотрены: знак и буквы чёткие, чёрного прямоугольника нет.

В portrait остаётся исходный фон со встроенным логотипом, без второго знака. Резерв перед действиями сохраняет область бренда; при крупном тексте/малой высоте контент прокручивается. При 360×800 и обычном размере текста кнопки видны сразу, без прокрутки, что проверено по PNG. Поля входа не добавлены на Welcome.

SafeArea учитывает верхнюю системную область и горизонтальную трёхкнопочную навигацию справа. Short-height, keyboard и accessibility text scale используют вертикальный scroll вместо RenderFlex overflow. При 200% текста на маленьком landscape legal-copy может требовать прокрутки, обе auth-кнопки доступны.

Общий `HeroFashionBackdrop` получил только optional `alignment`. Default остаётся `Alignment.center`; существующие экраны не меняют отображение. Welcome в landscape передаёт `Alignment.topCenter`.

## Проверка 20 октября 2026 года

**13 widget-тестов успешно**. Проверены:

- 844×390, 568×320 и portrait 360×800 при 100%, 130% и 200% текста;
- 568×320/200% с keyboard inset 140 px и 360×800/200% с inset 350 px;
- узкий landscape 480×320/200%; системные insets top 24 px, navigation right 48 px либо portrait bottom 48 px;
- реальные переходы обеих кнопок к `AuthScreen`: «Войти» открывает login, «Создать аккаунт» — registration;
- portrait → landscape → portrait без замены Welcome Element, потери контроллера или сохранённого email; после поворота оба перехода продолжают работать.

Проверки проходят без framework exceptions; auth-кнопки hit-testable и не попадают под системную навигацию. Использован локальный FakeApi, рабочий сервер не вызывался. Создано **12 диагностических PNG** с bundled production fonts и просмотрены обычный/увеличенный landscape, узкий fallback и portrait. Это widget rendering, не доказательство на физическом телефоне.

```powershell
& C:/src/flutter/bin/flutter.bat test test/welcome_landscape_test.dart `
  --dart-define=WELCOME_PREVIEW_OUTPUT=.tmp/welcome-landscape-20261020 `
  --reporter expanded
```

Лог: `.tmp/welcome-landscape-20261020.log`. PNG: `.tmp/welcome-landscape-20261020/`. Без `WELCOME_PREVIEW_OUTPUT` те же тесты выполняют layout/navigation проверки без записи изображений.

Backend, сервер, signing и deployment этим изменением не затронуты.
