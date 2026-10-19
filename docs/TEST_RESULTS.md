# Результаты проверки — 19 октября 2026

**Актуальный прогон после Kafka: 139 passed за 44,44 s, покрытие 95,45%, включая два теста с настоящим broker.** Отдельно проверены остановка Kafka, сохранение outbox при записи через HTTP, перезапуск Goods и доставка после восстановления. Контейнеры после проверок остановлены, volumes сохранены. [Подробный текущий отчёт](TEST_REPORT_LATEST.md), [Kafka-контракты](KAFKA_CONTRACT.md), [сверка B2B с REST-контрактом](B2B_CONTRACT_AUDIT.md).

Ниже сохранены результаты предыдущих локальных проверок и первоначального запуска Compose.

Проверки выполнены локально на macOS ARM64, Python 3.12.13, PostgreSQL 16.2. PostgreSQL работал в отдельном временном каталоге; тестовые данные находились только в отдельных `*_test` БД.

## Автоматические проверки

Полная команда `sh scripts/test.sh` с `TEST_POSTGRES_URL`:

- **111 passed**, без пропусков и предупреждений.
- Общее покрытие строк и ветвлений: **95,68%**; порог runner — 85%.
- Ruff lint и format — прошли.
- Все три Alembic upgrade/downgrade/upgrade и `alembic check` — прошли.
- HTTP-тесты используют три настоящих FastAPI-приложения, реальные SQLAlchemy/asyncpg операции в PostgreSQL и typed httpx-клиенты между ними.
- Конкурентное создание на границе 10 товаров / 5 публикаций: из четырёх запросов ровно один успешный, остальные QUOTA_EXCEEDED; число записей не превышено.
- Проверены одновременный onboarding, UNIQUE конфликты, сохранение состояния при отклонённом PATCH, отказ commit до отправки успешного ответа и изоляция DB roles.

Полный вывод этого локального прогона: [test-results/pytest-postgres.txt](test-results/pytest-postgres.txt). Coverage HTML генерируется в `htmlcov/index.html`; конфигурация coverage включает ctrace и greenlet/thread, чтобы корректно учитывать возобновление SQLAlchemy async-кода.

| Файл тестов | Проверяемая область |
|---|---|
| test_seller_flow.py | Onboarding, профиль, CRUD, квоты, поиск, пагинация, rollback |
| test_publications.py | Draft/publish, ordered links, unlink, validation, stored XSS |
| test_security.py | Два продавца, buyer/seller, JWT, CSRF, CORS, internal key, OpenAPI |
| test_integrations.py | Частичные отказы, Payment proof/repeat/expiry, Order permissions/transitions, timeout |
| test_contracts.py | HTTP wire contracts, typed errors, owner read APIs, seller batch/privacy |
| test_media.py | MIME/bytes/format/pixel limits, повреждённые файлы, compensation, S3 adapter |
| test_postgres.py | Настоящие миграции, constraints, модели, гонки квот, DB permissions |
| test_additional_behaviors.py | Premium, отказ policy, dashboard, checkout idempotency, commit failure |
| test_scripts.py | Идемпотентный seed и smoke через HTTP всех трёх приложений |

Быстрый набор без PostgreSQL также выполнен: соответствующие PostgreSQL-only тесты явно пропускаются. Для окончательного результата выше использована именно PostgreSQL, без SQLite.

## Отдельные процессы и реальная локальная сеть

Дополнительно запущены три отдельных uvicorn-процесса с HTTP-взаимодействием через localhost, каждый со своим DSN и ограниченной ролью `b2b`/`goods`/`publications`. Миграции выполнены этими же ролями, без выдачи CREATE на всю БД.

У всех трёх приложений успешно проверены `/healthz`, `/readyz`, `/docs`, `/openapi.json`. Seed выполнен дважды через настоящий HTTP и реальный boto3 S3-протокол к тестовому HTTP-серверу moto. После повторного запуска: **3 товара, 2 опубликованные записи, 1 S3-объект**. Не создавались реальные пользовательские аккаунты, платежи или заказы.

Read-only сетевой smoke: **50 запросов**, concurrency **5**, duration **0,136 s**, p50 **11,09 ms**, p95 **24,53 ms**. Это единичное измерение на локальном маленьком наборе с dev адаптерами; оно не подтверждает SLA, коммерческую нагрузку или 100 одновременных пользователей.

## Запуск Docker Compose

После установки Docker Desktop выполнены `docker compose up --build -d`, миграции при старте и seed. B2B, Goods, Publications и PostgreSQL имеют статус healthy. `/readyz`, `/docs`, `/openapi.json` всех трёх приложений вернули HTTP 200. Проверены seller profile/products/posts/dashboard через B2B и internal списки Goods/Publications с сервисным ключом.

Полный runner `docker compose --profile test run --build --rm tests` выполнен в отдельной PostgreSQL test-БД: **113 passed**, покрытие **95,68%**, Ruff lint/format прошли. Добавлены два теста идемпотентной инициализации bucket, ограниченной public-read policy и отказа при недоступных правах. [Полный Docker-прогон](test-results/pytest-docker.txt).

MinIO успешно собран из закреплённого официального исходного релиза `RELEASE.2025-04-22T22-12-26Z`: прежние образы Docker Hub недоступны, Quay вернул 401. Вместо образа mc инициализацию bucket выполняет Python/boto3. Контейнер init завершился с кодом 0; изображение seed действительно загружено в MinIO и прочитано по публичному URL с HTTP 200. Созданы 3 товара и 2 опубликованные записи. [Результат сетевой проверки](test-results/docker-live.json).

По запросу пользователя локальные копии frontend и подготовленный frontend-патч удалены, процесс Next остановлен. После первоначального запуска и повторных тестов backend-контейнеры также остановлены; данные сохранены.

## Что не проверено и осталось внешним

- Боевой S3 не подключён. Проверены boto3/moto и настоящий MinIO в Docker по S3-протоколу.
- Реальные Users/Auth, Order/Payment, Analytics остальных участников не подключены. Проверены согласованные модели и предложенные internal-контракты с test/dev adapters; согласование и межкомандный стенд перечислены в INTEGRATIONS.md.
- Pickup-code/fulfillment actions, YooKassa/CDEK, moderation, buyer pages/feed/follow/like и production gateway/TLS принадлежат другим частям проекта или явно исключены из MVP. Они не объявляются реализованными этим backend.
- Неучтённые S3-объекты после сбоя между put и записью metadata требуют inventory/manual cleanup в maintenance window. CLI очищает только отслеживаемые неиспользуемые media с проверкой всех владельцев ссылок.

До обязательного уточнения пользователя Kafka в предыдущих прогонах не разворачивалась. Сейчас реализована и проверена; предлагаемые события и результаты описаны в KAFKA_CONTRACT.md и актуальном отчёте выше. Успех fake checkout не трактуется как реальное подтверждение Premium.
