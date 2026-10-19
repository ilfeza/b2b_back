# Последний прогон тестов — 19 октября 2026

**139 passed, 0 failed, 0 errors, 0 skipped. Время pytest — 44,44 секунды.** Ruff lint и format прошли: 96 файлов соответствуют форматированию. После сохранения отчётов контейнеры проекта остановлены; данные PostgreSQL, MinIO и Kafka в volumes сохранены.

## Окружение и команда

Docker Desktop на macOS ARM64; тестовый контейнер Linux, Python 3.12.15, pytest 9.1.1, настоящие PostgreSQL 16 и Apache Kafka 4.1.1 KRaft. Тесты создают отдельную `marketplace_test`, пересоздают в ней схемы и очищают таблицы между API-тестами. Kafka integration tests создают уникальные topics/groups и удаляют test topics после завершения. Демонстрационная `marketplace` не очищалась.

```sh
docker compose --profile test run --build --name handmade-kafka-tests tests \
  sh scripts/test.sh --junitxml=/app/kafka-tests.xml --cov-report=json:/app/kafka-coverage.json
```

Runner проверяет тестовую БД, Ruff, форматирование и pytest с порогом покрытия 85%. После завершения текстовый/JUnit/coverage/HTML отчёты сохранены на компьютере; одноразовый тестовый контейнер удалён. Команда сама не останавливает основные сервисы — после неё отдельно выполнен `docker compose stop`.

## Результаты по модулям

| Область | Тестов | Результат |
|---|---:|---|
| Seller flow: onboarding, CRUD, квоты, поиск, rollback | 20 | passed |
| Publications: draft/publish, ordered links, HTML sanitization | 15 | passed |
| Security: JWT, CSRF, роли, владельцы, CORS, service key | 25 | passed |
| Integrations: Payment/Order adapters, повторы, ошибки, timeout | 15 | passed |
| Contracts: HTTP DTO, typed errors, owner/batch/privacy | 9 | passed |
| Media: проверка изображений, лимиты, S3, compensation | 8 | passed |
| PostgreSQL: миграции, constraints, гонки квот, DB permissions | 6 | passed |
| Additional behaviors: Premium, dashboard, policy/commit failures | 11 | passed |
| Scripts: идемпотентный seed и HTTP smoke | 2 | passed |
| MinIO init: идемпотентность, public-read policy, access denied | 2 | passed |
| Events: outbox/inbox, duplicate concurrency, retry, DLQ, commit failure, shutdown | 24 | passed |
| Real Kafka: HTTP domain actions → broker → consumers, duplicate/DLQ/recovery/restart | 2 | passed |
| **Итого** | **139** | **Все прошли** |

## Покрытие

Измеряется пакет `marketplace`. Скрипты выполняются в тестах, но не входят в процент покрытия — так настроен `tool.coverage.run.source` в pyproject.toml.

| Метрика | Результат |
|---|---:|
| Исполняемые строки | **97,02%** — 2054 из 2117 |
| Ветвления | **85,84%** — 297 из 346 |
| Суммарное покрытие строк и ветвлений | **95,45%** |
| Порог runner | 85% — выполнен |
| Непокрытые исполняемые строки / исходы ветвлений | 63 / 49 |

Покрытие показывает, что код выполнялся тестами; оно не доказывает отсутствие ошибок или буквальное соответствие исходному API-контракту.

## Файлы результатов

- [Полный вывод Docker-прогона](test-results/kafka-docker.txt).
- [JUnit XML: каждый тест](test-results/junit-latest.xml).
- [Машинная сводка](test-results/summary-latest.json).
- [Coverage JSON](test-results/coverage-latest.json).
- [HTML-отчёт покрытия](../htmlcov/index.html), обновлён из последнего тестового контейнера.
- [Подтверждение остановки контейнеров](test-results/containers-stopped.json).
- [Фактические ответы B2B до остановки](test-results/b2b-contract-observed.json).
- [Аудит B2B по исходному контракту](B2B_CONTRACT_AUDIT.md).
- [Остановка/восстановление настоящего broker и перезапуск Goods](test-results/kafka-outage-live.json).
- [Outbox, Kafka partition/offset и consumer receipts](test-results/kafka-operations-live.json).
- [Сообщение из штатного Apache Kafka console consumer](test-results/kafka-console.txt).
- [Журналы работающих сервисов](test-results/kafka-runtime.log).
- [Контракты событий и команды воспроизведения](KAFKA_CONTRACT.md).

## Что такое htmlcov

`htmlcov` — генерируемая папка статического HTML-отчёта coverage.py/pytest-cov. Откройте `htmlcov/index.html`: видны файлы, проценты покрытия и непроверенные строки/ветвления внутри каждого файла.

Это результат тестирования, а не frontend приложения и не служебные данные backend. Для чтения Docker и запущенные сервисы не нужны. Папку можно удалить; она пересоздаётся при прогоне с `--cov-report=html`. При тестировании внутри Docker отчёт нужно снова скопировать из контейнера на компьютер. Папка исключена из Git и Docker build context.

## Что подтверждено и что осталось внешним

Три настоящих FastAPI-приложения взаимодействуют typed httpx-клиентами через ASGITransport; операции БД выполняются PostgreSQL/asyncpg. Goods и Publications не подменены fake domain services. Проходят все три Alembic upgrade/downgrade/upgrade и `alembic check`, ограничения и DB role isolation. Конкурентное создание на границе лимитов 10 товаров / 5 публикаций допускает ровно один из четырёх запросов. Проверены ownership, JWT/CSRF, sanitization, частичные отказы, checkout idempotency и запрет Premium без доверенного подтверждения.

Реальные Users/Auth, Order/Payment, Analytics других команд не подключены: их поведение проверено на адаптерах и HTTP MockTransport. S3 adapter в pytest использует boto3/moto; настоящий MinIO был отдельно проверен в Compose, см. [сетевой отчёт](test-results/docker-live.json). Боевой S3, внешние YooKassa/CDEK, production gateway/TLS и SLA не подтверждены. Браузерные сценарии не входят в этот прогон. Kafka проверена на настоящем broker; внешнее order.status_changed выключено до согласования.

Отдельно выполнен `python -m scripts.kafka_check --outage`: настоящий Kafka broker остановлен, товар и опубликованный пост созданы через HTTP, у обоих зафиксированы unpublished outbox rows. Goods перезапущен до восстановления broker. После его запуска все шесть product/publication событий получили publishedAt и были найдены в B2B inbox с исходными eventIds. Seller.created также подтверждён receipts Goods и Publications. Консольный consumer прочитал product.created по тому же topic/partition/offset, который сохранён в B2B receipt; JSON совпал с envelope outbox.

24 новых event-теста включают атомарный rollback при отказе outbox, конкурентную дедупликацию, координацию двух publishers PostgreSQL lock, повтор после broker ack + отказ DB commit, quarantine без секретов в логах, инфраструктурные retry, optional order contract, принудительное закрытие обоих Kafka clients при shutdown timeout и единственный seller.created при четырёх одновременных onboarding-запросах. Два real-Kafka теста не используют fake Kafka producers/consumers.

Исходному REST DOCX реализация соответствует не полностью — предыдущие расхождения описаны в отдельном аудите. Kafka-расширение не объявляется согласованным другими командами.

**Текущее состояние: все контейнеры проекта остановлены, volumes сохранены. Swagger на localhost будет недоступен до следующего `docker compose up -d`.**
