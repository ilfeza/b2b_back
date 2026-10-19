# Kafka: предлагаемые контракты событий, версия 1

Реализовано по обязательному указанию пользователя от 19 октября 2026. **Названия topics, event types и payloads пока являются предложением для согласования с другими разработчиками.** Реализация этих событий внутри нашей команды не означает согласование с Order, Users или B2C.

Kafka дополняет REST. Права продавца, принадлежность media, действующий тариф и конкурентные квоты по-прежнему проверяются синхронно через HTTP и PostgreSQL locks. Kafka, inbox и activity не используются для принятия этих решений.

## Запуск

```sh
cp -n .env.example .env
docker compose up --build -d
```

Поднимаются PostgreSQL, настоящий MinIO, Apache Kafka **4.1.1** в KRaft combined broker/controller mode, init topics/bucket и три FastAPI-сервиса. ZooKeeper не используется. Брокер работает от appuser; данные лежат в `kafka-data` в `/var/lib/kafka/data`. Приложения получают миграции 0002 с event tables при старте. Kafka bootstrap: `kafka:29092` внутри Compose, `localhost:9092` с компьютера. REST/Swagger сохраняют порты 8080/8081/8082.

Одноразовый `kafka-init` создаёт topics идемпотентно через aiokafka Admin. Автоматическое создание неизвестных topics брокером выключено. Для локального стенда topics имеют 3 partition, replication factor 1, retention 7 суток. Это один dev broker без высокой доступности; потеря его volume не покрывается гарантией доставки. Образы/настройки не объявляются production-кластером.

## Topics и группы

| Topic по умолчанию | Producer | Consumer / group |
|---|---|---|
| `handmade.sellers.v1` | B2B | Goods: `handmade.goods.v1`; Publications: `handmade.publications.v1` |
| `handmade.products.v1` | Goods | B2B: `handmade.b2b.v1` |
| `handmade.publications.v1` | Publications | B2B: `handmade.b2b.v1` |
| `handmade.orders.v1` | Внешний Order Service, после согласования | B2B, только при `KAFKA_ORDERS_ENABLED=true` |
| `handmade.dlq.b2b.v1` | B2B outbox | Ручной разбор / будущий согласованный replay tool |
| `handmade.dlq.goods.v1` | Goods outbox | Ручной разбор |
| `handmade.dlq.publications.v1` | Publications outbox | Ручной разбор |

У сервисов разные group IDs. Реплики одного сервиса используют одинаковый group ID, чтобы Kafka распределяла partitions. Goods и Publications получают seller.created независимо. B2B не читает собственный seller topic и не подписывается на DLQ. Orders topic по умолчанию не создаётся и не читается; флаг нужен и для init, и для B2B.

## Envelope и partition key

UTF-8 JSON без токенов, контактных данных, HTML, полного каталога и изображений. Kafka key — UTF-8 `aggregateId`; отсутствие ключа или несовпадение с envelope отклоняется.

```json
{
  "eventId": "ba50fe39-39c9-44d4-a833-2a3330a87016",
  "eventType": "product.created",
  "version": 1,
  "occurredAt": "2026-10-19T17:00:00Z",
  "aggregateId": "f0181cf6-2d89-4f3d-9759-a3fedeea662a",
  "payload": {
    "productId": "f0181cf6-2d89-4f3d-9759-a3fedeea662a",
    "sellerId": "3731edfe-c8eb-4525-8fab-9add0f31c208"
  }
}
```

| Поле | Требование |
|---|---|
| eventId | UUID; создаётся один раз внутри транзакции и сохраняется при всех повторных отправках |
| eventType | Один из типов таблицы ниже; дополнительно проверяется соответствие topic и подписке consumer |
| version | Строго целое `1`; неизвестная версия идёт в DLQ |
| occurredAt | ISO 8601 с timezone; нормализуется в UTC |
| aggregateId | Строка ID сущности; должна совпадать с ID в payload |
| payload | Строгий Pydantic DTO; неизвестные поля запрещены |

Product/publication/seller IDs — UUID. Для будущего Order допускается строковый ID `[a-zA-Z0-9_-]` до 100 символов. Pydantic discriminator — eventType. Нормативная машинная схема: [events.v1.schema.json](contracts/events.v1.schema.json), реализация: [schema.py](../marketplace/shared/messaging/schema.py). Export выполняется `uv run python -m scripts.export_openapi`.

## События и payloads

Во всех payloads sellerId — UUID. Остальные ID также UUID, кроме orderId. Ниже перечислены полные поля payload, а не snapshot доменной модели.

| eventType | aggregateId | Поля payload | Когда создаётся |
|---|---|---|---|
| `seller.created` | sellerId | sellerId, ownerUserId, username | Только при переходе магазина pending → active, после подтверждения роли Users |
| `product.created` | productId | productId, sellerId | После создания товара |
| `product.updated` | productId | productId, sellerId, changedFields | После фактического изменения товара; пустой/не меняющий значения PATCH не создаёт событие |
| `product.deleted` | productId | productId, sellerId | После soft delete товара |
| `publication.published` | publicationId | publicationId, sellerId, status=`published` | Создание сразу published или переход draft → published, включая повторную публикацию после снятия |
| `publication.updated` | publicationId | publicationId, sellerId, status, changedFields | Фактический PATCH draft/published; переход draft → published представлен только publication.published |
| `publication.deleted` | publicationId | publicationId, sellerId | Soft delete draft или опубликованного поста |
| `order.status_changed` — выключено | orderId | orderId, sellerId, previousStatus, status | Предлагаемое событие от внешнего Order Service |

`username`: `[a-z][a-z0-9_]{2,31}`. `changedFields` содержит непустой список camelCase имён изменённых полей. Для товара: title/description/image/price/saleMode/availableDeliveries/pickupPoints/dealContacts/inStock. Для публикации: title/body/coverImage/status/productIds. Значения полей не передаются. status публикации — draft/published; Order statuses — created/paid/shipped/delivered/cancelled.

Создание незавершённого draft не порождает отдельного publication.created: такого типа нет в запрошенном списке. Обновление draft создаёт publication.updated. Повторный идемпотентный onboarding не создаёт seller.created повторно. Неуспешные запросы/откаты транзакции событий не создают. Уже существовавшие до миграции магазины, товары и посты автоматически не backfill-ятся.

## Что делают consumers

Каждый consumer в своей схеме сохраняет receipt в `event_inbox` и атомарно увеличивает `event_activity.processed_count` для `(consumer_group,seller_id,event_type)`, обновляет `last_occurred_at`. Receipt содержит eventId, type, aggregateId, sellerId, occurredAt, topic/partition/offset и hash сообщения. **Полные payload товара/публикации/заказа в B2B не сохраняются.**

Это реальная устойчивая обработка с наблюдаемым результатом: счётчик и receipt остаются после перезапуска приложения. Показатели доступны через internal operations API. Они предназначены для аудита получения событий; это не точное количество текущих товаров, не тарифные квоты и не замена API владельца данных.

Будущее order.status_changed обновляет только эти метаданные. Оно не создаёт локальные заказы, не меняет статус через сторонний Order API и не подтверждает оплату/Premium. Для добавления конкретных бизнес-реакций нужен согласованный внешний контракт.

## Надёжность и порядок

1. В той же DB-транзакции, что и доменная запись, INSERT создаёт `event_outbox`. API commit фиксирует обе записи или ни одной. Резервация pending-магазина при onboarding может сохраняться отдельно; seller.created появляется только вместе с активированием магазина.
2. Lifespan FastAPI запускает два background task: outbox publisher и consumer. Подключение к Kafka не блокирует HTTP startup. Kafka off/retry не влияет на синхронные проверки прав/квот и не превращает обычный CRUD в синхронную зависимость от брокера.
3. Publisher читает oldest unpublished outbox row. PostgreSQL advisory transaction lock выбирает одного publisher на схему даже при нескольких API workers. Key обеспечивает partition affinity одного aggregate. До подтверждения первого pending события следующие не отправляются; глобальный head-of-line retry здесь сознательно выбран ради простого порядка. Порядок между разными topics не гарантируется.
4. Используется aiokafka producer с `acks=all` и `enable_idempotence=true`. Outbox отмечается published только после broker ack и DB commit. При таймауте/сбое nextAttemptAt и attempts сохраняются; retry с exponential backoff, по умолчанию 1–30 s, без конечного отказа от критичных событий. Уже отправленные записи не удаляются автоматически.
5. Если broker принял событие, а DB commit/процесс упал до записи publishedAt, возможна повторная отправка с тем же eventId. Поэтому гарантия транспорта — **at least once**, а не exactly once между PostgreSQL и Kafka.
6. Consumer выключает auto commit. INSERT inbox с уникальным `(consumer_group,event_id)` и изменение activity выполняются в одной транзакции. Повторный ID с тем же canonical envelope — no-op; тот же ID с другим содержимым — event_id_collision и DLQ. Гарантируется однократный DB-эффект внутри группы, в том числе при конкурентных повторах.
7. Kafka offset `record.offset+1` фиксируется только после DB commit успешной обработки либо durable DLQ outbox. Ошибка commit/rebalance приводит к replay; inbox защищает от повторного эффекта. Обработка последовательная, по одной записи; незавершённый DB эффект не подтверждается.
8. Ошибки БД/timeout/сети при обработке повторяются без пропуска события. Остальные сбои handler повторяются до 3 попыток, с сохранением attempts; затем DLQ. Успешное восстановление помечает failure как recovered.
9. При shutdown task отменяются, транзакции откатываются, Kafka clients закрываются до DB/HTTP clients. Есть ограничение времени закрытия и аварийное закрытие соединений aiokafka 0.13; Compose даёт 30 s grace. Повторный запуск runtime использует прежний group ID и inbox.

Kafka producers не используют распределённую транзакцию с PostgreSQL; надёжность обеспечивают local outbox + ack + inbox. Advisory lock IDs: B2B=703001, Goods=703002, Publications=703003, область действия — своя PostgreSQL БД. Наличие event tables проверено Alembic roundtrip/model consistency tests. Outbox записывается даже при KAFKA_ENABLED=false; флаг отключает workers, а не сохранение событий.

Inbox/outbox/failures не очищаются автоматически. Срок хранения inbox при будущей очистке должен покрывать возможный replay и Kafka retention; удаление inbox допускает повторный эффект старого сообщения. Публикации payload не содержат revision/snapshot; потребитель, которому нужно текущее состояние ресурса, обязан читать owner API. occurredAt используется для last activity через max, а не для изменения бизнес-состояния.

## Некорректные сообщения и DLQ

Невалидный JSON, размер выше лимита, неизвестные version/type, неверные UUID/timezone/aggregateId/key и недопустимый topic/type считаются неисправимыми. В `event_failures` сохраняется reason code, hash и location; raw exception/input в лог не выводятся. В той же транзакции создаётся DLQ outbox event `message.rejected` с envelope версии 1. Его eventId детерминирован по consumer group + topic + partition + offset. Повтор одного source offset не создаёт вторую DLQ-запись.

Payload message.rejected:

```json
{
  "consumerGroup": "handmade.b2b.v1",
  "originalTopic": "handmade.products.v1",
  "originalPartition": 0,
  "originalOffset": 42,
  "reasonCode": "invalid_schema",
  "attempts": 1,
  "originalSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "originalSize": 100,
  "originalValueBase64": "...",
  "truncated": false
}
```

DLQ включает не более первых **4096 bytes** исходного сообщения в base64; для больших сообщений truncated=true и полный SHA-256. Это ограничивает размер DLQ и не позволяет poison message блокировать его публикацию из-за размера. Для полного replay используйте source topic/partition/offset в пределах retention. Base64 не является шифрованием: доступ к DLQ и outbox operations предназначен только доверенным операторам. Рекурсивного consumer для DLQ нет.

Неисправимые сообщения не заставляют API выдавать успешный результат handler, но их подтверждённый durable quarantine позволяет consumer перейти к следующей записи. DLQ также отправляется через outbox: недоступность Kafka после quarantine не теряет сообщение.

## Конфигурация

| Переменная | Default / назначение |
|---|---|
| KAFKA_ENABLED | true; false для unit tests/maintenance, outbox продолжает записываться |
| KAFKA_BOOTSTRAP_SERVERS | localhost:9092 вне Compose |
| KAFKA_DOCKER_BOOTSTRAP_SERVERS | kafka:29092, Compose override адреса для контейнеров |
| KAFKA_B2B_GROUP_ID / KAFKA_GOODS_GROUP_ID / KAFKA_PUBLICATIONS_GROUP_ID | Три разные группы из таблицы topics |
| KAFKA_SELLERS_TOPIC / KAFKA_PRODUCTS_TOPIC / KAFKA_PUBLICATIONS_TOPIC | Topics доменных событий |
| KAFKA_ORDERS_TOPIC / KAFKA_ORDERS_ENABLED | handmade.orders.v1 / false; только после согласования |
| KAFKA_B2B_DLQ_TOPIC / KAFKA_GOODS_DLQ_TOPIC / KAFKA_PUBLICATIONS_DLQ_TOPIC | Отдельные DLQ topics |
| KAFKA_POLL_INTERVAL_SECONDS | 0.5, polling outbox |
| KAFKA_RETRY_BASE_SECONDS / KAFKA_RETRY_MAX_SECONDS | 1 / 30, exponential backoff |
| KAFKA_DELIVERY_TIMEOUT_SECONDS | 5, ограничение ожидания start/send в одной попытке |
| KAFKA_SHUTDOWN_TIMEOUT_SECONDS | 10, graceful client stop |
| KAFKA_HANDLER_MAX_ATTEMPTS | 3, ошибки handler; не применяется к инфраструктурным сбоям |
| KAFKA_MAX_EVENT_BYTES | 262144, лимит входного сообщения |

Settings проверяет уникальность имён topics/groups, положительные timeout/лимиты. Нельзя случайно назначить разным сервисам одну consumer group. Локальные listeners — PLAINTEXT и loopback на хосте. Внешний production broker и его TLS/SASL/ACL потребуют отдельной конфигурации стенда; текущая реализация не заявляет доступ к такому broker.

## Наблюдение через REST и Kafka CLI

На каждом сервисе, с `X-Service-Key` из `.env`:

- GET `/api/internal/events?limit=20`: unpublished/published counts, последние outbox metadata, receipts, activity и DLQ count. producerConnected/consumerConnected показывают состояние запуска клиентов, не являются независимой healthcheck доступности брокера.
- GET `/api/internal/events/outbox/{eventId}`: полный отправляемый envelope. Raw poison snippet DLQ также доступен только с internal key.

```sh
curl -H 'X-Service-Key: dev-only-internal-key-change-before-deployment' \
  http://localhost:8080/api/internal/events
curl -H 'X-Service-Key: dev-only-internal-key-change-before-deployment' \
  http://localhost:8081/api/internal/events
```

В outbox/receipts operational API имена DB-метаданных snake_case (`event_id`, `aggregate_id`, `published_at`); сам Kafka envelope и endpoint outbox/{eventId} — camelCase по схеме. HTTP health/readiness проверяет приложение/БД, а не требует Kafka для каждого CRUD; pending queue и connection state видны отдельно.

```sh
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:29092 --topic handmade.products.v1 \
  --from-beginning --max-messages 1 --timeout-ms 15000
```

Consumer CLI не использует группу приложения. Сохранённый DB publishedAt + wire message + B2B receipt с тем же eventId подтверждают путь доставки.

## Воспроизводимые проверки

Полный pytest с PostgreSQL и настоящим Kafka:

```sh
docker compose --profile test run --build --rm tests
```

Без контейнера для тестового процесса:

```sh
TEST_POSTGRES_URL=postgresql+asyncpg://postgres:local-postgres-only@localhost:5432/handmade_kafka_test \
TEST_KAFKA_BOOTSTRAP_SERVERS=localhost:9092 uv run sh scripts/test.sh
```

Kafka integration tests используют уникальные topics/groups каждого теста и удаляют эти topics после остановки workers. Без TEST_KAFKA_BOOTSTRAP_SERVERS либо PostgreSQL они явно пропускаются. Compose test profile и CI передают обе переменные.

Реальное действие через запущенные HTTP-сервисы, без fake Kafka:

```sh
uv run python -m scripts.kafka_check --output docs/test-results/kafka-compose-demo.json
```

Автоматическая проверка остановки **настоящего** broker, записи доменных изменений при остановке, перезапуска Goods и доставки после восстановления:

```sh
uv run python -m scripts.kafka_check --outage --output docs/test-results/kafka-outage-live.json
```

Проверки работают только с localhost и явно включёнными dev adapters. Создают/активируют второй demo-магазин, загружают изображение, создают/изменяют/удаляют тестовые товар и публикацию. Проверяют publishedAt и получение всех шести Goods/Publications событий B2B с теми же eventIds. Изображение остаётся для обычной media cleanup. Флаг --outage кратковременно останавливает Kafka и перезапускает Goods; в finally восстанавливает broker при ошибке. Повторная регистрация магазина идемпотентна. Сбой проверки может оставить тестовые записи — вывод сообщает ошибку, а не ложный passed.

Логи содержат фиксированные коды/имена классов ошибок; payload, JWT, service keys, raw Pydantic errors и SQL parameters туда не передаются. Тесты проверяют отсутствие входного секрета в логах.

Результат реализации: [139 passed в Docker](test-results/kafka-docker.txt), [отказ/восстановление настоящего broker](test-results/kafka-outage-live.json), [сопоставление outbox → wire → receipt](test-results/kafka-operations-live.json). [Сводный отчёт](TEST_REPORT_LATEST.md).

Технические основания: [aiokafka producer](https://aiokafka.readthedocs.io/en/stable/producer.html), [manual offset commit](https://aiokafka.readthedocs.io/en/stable/consumer.html), [официальный Kafka Docker example](https://github.com/apache/kafka/blob/4.1.1/docker/examples/docker-compose-files/single-node/plaintext/docker-compose.yml).
