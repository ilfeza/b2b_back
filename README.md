# Handmade Marketplace: B2B, Goods, Publications

Три самостоятельных приложения Python 3.12 + FastAPI в одном репозитории. B2B предоставляет кабинет продавца и вызывает Goods и Publications **по HTTP**. PostgreSQL 16 содержит изолированные схемы `b2b`, `goods`, `publications`, каждая со своими миграциями и ролью БД. Товары и публикации в B2B не дублируются.

Реализованы onboarding с восстановлением после частичного отказа Users, профиль, доставка, изображения S3, тарифы и подтверждение Premium, конкурентные квоты, CRUD и поиск товаров, черновики и публикация безопасного HTML, упорядоченные связи с товарами, dashboard, подписчики и фасад заказов. Пользователей, заказы, платежи, follow/like и B2C этот репозиторий не хранит.

## Структура

```text
marketplace/
  b2b/           api, schemas, models, repositories, services, integrations
  goods/         api, schemas, models, repositories, services
  publications/  api, schemas, models, repositories, services
  shared/        конфигурация, DTO, HTTP-клиенты, БД, авторизация, ошибки
migrations/      три независимые истории Alembic
tests/           API, security, contracts, media, PostgreSQL/concurrency
scripts/         seed, smoke/performance, cleanup, тесты, OpenAPI
Dockerfile, Dockerfile.test, compose.yaml
```

## Запуск

Нужны Docker с Compose v2; для запуска вне контейнеров — Python 3.12 и uv.

```sh
cp .env.example .env
docker compose up --build -d
```

Миграции каждого приложения применяются при старте его контейнера. Отдельный скрипт: `sh scripts/migrate.sh b2b|goods|publications` с DSN выбранного сервиса в DATABASE_URL. MinIO init создаёт bucket и разрешает чтение изображений. Kafka 4.1.1 запускается в KRaft, kafka-init создаёт topics. Пароли и ключи в Compose предназначены для локальной разработки.

MinIO собирается из закреплённого официального исходного релиза: прежние образы MinIO/mc больше недоступны для публичного скачивания. Первая сборка требует загрузки Go toolchain и зависимостей; последующие используют Docker cache. Инициализация bucket выполняется нашим Python-скриптом через S3 API.

| Компонент | Адрес | Swagger |
|---|---|---|
| B2B | http://localhost:8080/api | http://localhost:8080/docs |
| Goods | http://localhost:8081/api | http://localhost:8081/docs |
| Publications | http://localhost:8082/api | http://localhost:8082/docs |
| MinIO API / console | http://localhost:9000 / http://localhost:9001 | — |
| Kafka bootstrap | localhost:9092; внутри Compose kafka:29092 | — |

Goods/Publications доступны на loopback для разработки; в общем окружении их следует оставить во внутренней сети. `/healthz`, `/readyz`, `/docs`, `/openapi.json` доступны у каждого приложения без `/api`. Readiness проверяет наличие таблиц после миграции и соединение с БД.

Compose явно включает **dev auth и fake-адаптеры только для чужих Users, Orders, Payment, Analytics**. S3/MinIO, Goods, Publications и все базы остаются настоящими. `demoData=true` отмечает демонстрационные метрики. Fake payment URL не ведёт к реальному платежу и не активирует Premium.

```sh
docker compose exec b2b python -m scripts.seed --base-url http://localhost:8000
```

Seed идемпотентен: создаёт магазин, PNG в MinIO, 3 товара, 2 опубликованных записи и доставку. Разрешён только в development с обоими dev-флагами. Тариф нового магазина Free; seed не меняет уже подтверждённый тариф.

Dev identity: заголовок `X-User-Id: 11111111-1111-4111-8111-111111111111` (buyer+seller). Пользователь `22222222-2222-4222-8222-222222222222` начинает как buyer и может пройти onboarding. Произвольные идентификаторы и заголовки ролей не принимаются. В production dev/fake-флаги запрещены валидатором конфигурации.

Проверка:

```sh
curl -H 'X-User-Id: 11111111-1111-4111-8111-111111111111' http://localhost:8080/api/seller/dashboard
```

Для ручной проверки откройте [Swagger B2B](http://localhost:8080/docs), выберите endpoint → **Try it out**, укажите `X-User-Id: 11111111-1111-4111-8111-111111111111` и нажмите **Execute**. Начните с `GET /api/seller/products` или `GET /api/seller/profile` после seed. В [Goods](http://localhost:8081/docs) и [Publications](http://localhost:8082/docs) для internal API нажмите **Authorize** и введите `SERVICE_KEY` из локального `.env` (по умолчанию `dev-only-internal-key-change-before-deployment`). Seller ID можно получить из профиля B2B. Это локальная dev-авторизация; настоящий вход предоставляет Users/Auth.

## Запуск приложений вне Docker

```sh
uv sync --frozen
docker compose up -d postgres minio minio-init
uv run alembic -c alembic-b2b.ini upgrade head
DATABASE_URL=postgresql+asyncpg://goods:goods@localhost:5432/marketplace uv run alembic -c alembic-goods.ini upgrade head
DATABASE_URL=postgresql+asyncpg://publications:publications@localhost:5432/marketplace uv run alembic -c alembic-publications.ini upgrade head
uv run uvicorn marketplace.b2b.main:factory --factory --port 8080
```

В отдельных терминалах:

```sh
DATABASE_URL=postgresql+asyncpg://goods:goods@localhost:5432/marketplace uv run uvicorn marketplace.goods.main:factory --factory --port 8081
DATABASE_URL=postgresql+asyncpg://publications:publications@localhost:5432/marketplace uv run uvicorn marketplace.publications.main:factory --factory --port 8082
uv run python -m scripts.seed
```

При локальном запуске `.env` берётся из `.env.example`. Сервисные URL содержат ровно один `API_PREFIX`, по умолчанию `/api`. Можно согласованно поменять его на `/api/v1` во всех трёх приложениях и их base URLs. Gateway не должен повторно добавлять этот префикс.

Для работы Kafka при запуске Python-приложений вне контейнеров также запустите `docker compose up -d kafka kafka-init`. Host bootstrap в `.env.example` — `localhost:9092`. Если Docker Desktop установлен на macOS, но команда `docker` не найдена, добавьте CLI в текущий терминал: `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`.

## События Kafka

Все три сервиса используют aiokafka, transactional outbox в собственной PostgreSQL-схеме и consumers с durable inbox. Goods публикует product.created/updated/deleted; Publications — publication.published/updated/deleted; B2B публикует seller.created и получает изменения товаров/постов. Consumers сохраняют receipts и счётчики активности без копирования каталога/публикаций в B2B. Повторы не дают второго DB-эффекта. Права и квоты остаются синхронными HTTP-проверками.

Все события пока **предлагаемые контракты**. Внешнее order.status_changed выключено до согласования. Схемы, topics, groups, retry, DLQ, ограничения и команды наблюдения: [KAFKA_CONTRACT.md](docs/KAFKA_CONTRACT.md).

```sh
uv run python -m scripts.kafka_check --output docs/test-results/kafka-compose-demo.json
uv run python -m scripts.kafka_check --outage --output docs/test-results/kafka-outage-live.json
```

Первая команда выполняет реальные изменения через локальные API и проверяет получение событий B2B. Вторая дополнительно останавливает настоящий Kafka broker и перезапускает Goods до восстановления, проверяя сохранность очереди. Требует запущенного Compose и явных dev-адаптеров; создаёт demo-магазин/изображение и создаёт, изменяет, удаляет тестовые товар и пост. Для диагностики: `GET /api/internal/events` на любом сервисе с `X-Service-Key`. Для прослушивания Kafka CLI см. контракт.

## Тесты

Полный набор, включая настоящую PostgreSQL 16, миграции и гонки квот, одной командой:

```sh
docker compose --profile test run --build --rm tests
```

Без Docker, с доступной PostgreSQL 16:

```sh
TEST_POSTGRES_URL=postgresql+asyncpg://postgres:local-postgres-only@localhost:5432/marketplace_test uv run sh scripts/test.sh
```

Чтобы этот запуск включал настоящие Kafka integration tests, добавьте `TEST_KAFKA_BOOTSTRAP_SERVERS=localhost:9092`. Compose test profile и CI уже передают broker URL. Без него real-Kafka тесты явно пропускаются.

Тестовый runner создаёт только отдельную БД с суффиксом `_test`; удаляет и пересоздаёт в ней три схемы; очищает таблицы перед каждым API-тестом. **Нельзя указывать рабочую БД.** Тестам нужен пользователь с правами создания тестовой БД и схем. Тесты не требуют платных сервисов: внешний HTTP проверяется MockTransport, S3-адаптер — moto; взаимодействие B2B↔Goods↔Publications идёт реальными httpx-клиентами через ASGITransport.

Быстрый набор на SQLite без PostgreSQL:

```sh
uv run pytest -q
```

В нём PostgreSQL-тесты явно пропускаются. SQLite не подтверждает работу блокировок `FOR UPDATE` и PostgreSQL-миграций. HTML coverage: `htmlcov/index.html`. Зафиксированные реальные результаты находятся в [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).

```sh
uv run python -m scripts.smoke --requests 100 --concurrency 5
```

Smoke выполняет реальные GET-запросы после seed, печатает измеренные p50/p95. Соответствие нагрузке 100 одновременных пользователей и SLA не заявляется.

## Production и интеграция


Установите `ENVIRONMENT=production`, `AUTH_DEV_MODE=false`, `EXTERNAL_FAKE_MODE=false`, `SERVICE_KEY` длиной от 32 символов, `JWT_PUBLIC_KEY` (PEM RS256), `JWT_ISSUER`, `JWT_AUDIENCE`, URL реальных сервисов и S3 credentials. JWT берётся из HttpOnly cookie `auth-token`; проверяются подпись, issuer, audience, exp и sub, затем подтверждённый профиль в Users. Для cookie-мутаций обязательны разрешённый `Origin` и `X-CSRF-Protection: 1`. Cookie выдаёт Auth, B2B её не выпускает. TLS завершается на gateway/ingress; внутренние ключи передаются по защищённой сети/TLS.

Внешние неподтверждённые endpoints изолированы в адаптерах и перечислены в [INTEGRATIONS.md](docs/INTEGRATIONS.md). Требуется согласование с разработчиками Users/Auth, Order/Payment и B2C; запросы и схемы для обсуждения уже подготовлены. Репозиторий не отправляет сообщения коллегам автоматически.

## Документация

- [Архитектура и потоки](docs/ARCHITECTURE.md)
- [Все API и расширения контракта](docs/API.md)
- [Решения и расхождения](docs/DECISIONS.md)
- [Интеграции и контракты на согласование](docs/INTEGRATIONS.md)
- [Трассировка каждого REQ-4.2.x](docs/REQUIREMENTS_TRACEABILITY.md)
- [Проверки и ограничения](docs/TEST_RESULTS.md)
- [Последний прогон тестов](docs/TEST_REPORT_LATEST.md)
- [Сверка B2B с исходным API-контрактом](docs/B2B_CONTRACT_AUDIT.md)
- [Kafka: предлагаемые события и надёжность](docs/KAFKA_CONTRACT.md)

Очистка изображений: `uv run python -m scripts.media_cleanup` выводит кандидатов старше 7 дней. Применение требует остановки **всех записей** в трёх сервисах и `--apply --writes-paused`. Скрипт проверяет ссылки Goods/Publications по HTTP, сохраняет изображения исторических товаров и записей, прекращает работу при отказе источника. Неучтённые в БД S3-объекты после сбоя до сохранения метаданных проверяются отдельно по inventory в maintenance window; автоматического удаления таких объектов нет.
