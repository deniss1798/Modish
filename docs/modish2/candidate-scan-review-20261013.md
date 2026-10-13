# Независимая проверка тонкого сканирования кандидатов

13 октября 2026. Read-only review `candidate_scan.py`, `may_match_flat_size`, интеграции в `activation_service` и новых тестов. Reviewer не менял backend. Для проверки использована только SQLite в памяти.

## Найденный и устранённый дефект

Первый вариант `hydrate` повторял исходные `OFFSET/LIMIT` после того, как projection уже выбрала нужные ID. Воспроизводимый пример: `select(Product).order_by(Product.id).offset(2).limit(3)`, `batch_size=1` — полное чтение возвращает `2,3,4`, thin scan возвращал пустой список. Единственный текущий production caller не использует пагинацию, но helper был небезопасен для повторного использования.

Root исправила повторную гидратацию: `query.limit(None).offset(None).where(Product.id.in_(pending))`. Pagination применяется один раз в projection, остальные ограничения и join остаются. Добавлен regression для заданных пагинированных ID; исправление упаковывается в новый immutable v26, v25 не переписывается.

## Проверенные свойства текущего применения

- Projection содержит все поля, которые нужны `product_role` и выбору подтверждённого пользовательского слота. Raw merchant JSON не нужен для отрицательного token probe.
- Probe не признаёт размер подходящим. Для потенциальных совпадений остаются полные проверки систем, неоднозначных исходных tokens, наличия конкретного размера и всех admission gates.
- Полная гидратация повторяет scope исходного SQL. Порядок выдачи восстанавливается по ordered ID projection; новые SQL results не меняют его.
- Pending изменения `Product` при выключенном autoflush переводят чтение на прежний полный ORM путь. Незамеченные SQLAlchemy изменения JSON в уже загруженном объекте читаются из живой identity map.
- Размер batch ограничен 1–500, текущий default 200. SQL projection использует `yield_per=500`; candidate catalogue не ограничен первыми 1000 rows. Full hydration держит только batch возможных совпадений. Existing identity-map objects временно удерживаются snapshot, чтобы сохранить live JSON семантику.
- Projection cursor закрывается при нормальном завершении и при раннем останове через `closing`. Full hydration result закрывается до выдачи batch. Mandatory-role probe закрывает оба result при положительном раннем возврате.
- Вычисление identity после hard gates сохраняет правило, что отклонённый товар не резервирует duplicate key. Scorer cache и максимум 50 finalists/category не расширены этой оптимизацией.

До pagination fix независимо пройдены 29 size/candidate/WOW tests за 2,914 с. После fix Root сообщила о 11 проходящих candidate-scan тестах; общий тест точного v26 пакета и серверное измерение выполняет Root. Эти числа не являются подтверждением реального покрытия обувью или готовности релиза.

## Подготовленный серверный runner

Новый `scripts/intent_v26_remote.py` сохраняет отдельные pins baseline v24 и fresh importer v20. Fresh prerequisite — завершённый отчёт `refresh-20261013/refresh-report.json` в v20 release directory. Historical replay не поддерживается: сравнение должно использовать фактический единый UTC clock и неистёкшие наблюдения stock.

Для оптимизации одинакового числа карточек недостаточно: completed activation и ordered hashes колоды/первой ленты должны совпасть во всех трёх запусках. При изменении hashes сохраняется диагностический catalog report и stage завершается ошибкой. `product_ready` остаётся false до независимых продуктовых критериев.

Компиляция и 12 полностью mocked offline проверок runner пройдены: успешная orchestration, смена hash при том же count, истёкший stock, запрет production DB, закреплённая fresh зависимость, offline build/unit flags, единый clock, scoped cleanup при timeout. Реальные SSH, Docker и PostgreSQL из этих проверок не вызывались. [Preflight JSON](intent-v26-runner-preflight.json).

Положительные WOW-предметы проверяются на exact size/fresh stock, но pinned helper не возвращает hashes выбранных комплектов. Runner явно сохраняет это ограничение; равное число положительных образов не объявляется доказательством одинакового состава.
