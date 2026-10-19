# Интеграции и контракты для согласования

Статус внутренних контрактов Goods/Publications: **предложение этой команды**, реализовано и проверяется HTTP contract/API tests; нужно согласование с B2C и gateway. Внешние Users/Auth, Order/Payment, Analytics пока проверены с адаптерами, а не с работающими сервисами других участников.

Во всех запросах httpx: `X-Service-Key`, `X-Request-Id`, configurable timeout, redirect off, без автоматических повторов. Ответ `{data, error}`; DTO camelCase. Неисправный payload upstream даёт 502; timeout/5xx даёт 503. Внешние сообщения об ошибках не отражаются. `Idempotency-Key` передаётся при checkout подписки и смене статуса заказа.

## Наши сервисы

| Клиент | Base URL configuration | Назначение |
|---|---|---|
| GoodsClient | GOODS_SERVICE_URL | Собственный каталог: CRUD, seller list, ordered batch, low stock/counts |
| PublicationsClient | PUBLICATIONS_SERVICE_URL | Собственные публикации: draft, edit, publish, list, details |
| PolicyClient | B2B_SERVICE_URL | Подтверждённые entitlements, batch entitlements и владение media URL |

Внешние продавцу маршруты остаются у B2B `/products`, `/posts`, `/seller/...`. Все внутренние endpoints требуют service key. Префикс `/api` входит в base URL; typed client добавляет только относительный route.

Goods mutation: `/internal/sellers/{sellerId}/products[/{productId}]`, batch `/internal/sellers/{sellerId}/products/batch`, summary `/internal/sellers/{sellerId}/products-summary?threshold=3`. Seller ID назначается B2B по владельцу из cookie. Например:

```json
{"title":"Чашка","image":"http://localhost:9000/handmade-media/SELLER/OBJECT.png","price":1800,"saleMode":"deal_only","dealContacts":"Контакты мастера","inStock":3,"availableDeliveries":["pickup"],"pickupPoints":[{"city":"Москва","address":"Адрес 1"}]}
```

Потребителям B2C доступны owner read APIs, без алгоритма покупательской ленты:

- `GET /internal/products?q=&sellerId=&delivery=&page=&pageSize=`.
- `GET /internal/products/{id}`: удалённый товар 404.
- `POST /internal/products/batch`: `{ids: UUID[], includeDeleted: false}`, максимум 100. Ответ упорядочен как ids, отсутствующие пропущены. `includeDeleted=true` возвращает исторические tombstones с purchasable=false. Отдельные seller batch ограничены одним владельцем.
- `GET /internal/posts?status=...` не нужен: read API **всегда published**; параметры q, sellerId, page, pageSize.
- `GET /internal/posts/{id}`: draft/deleted/missing 404. Ответ содержит HTML, status, productIds и publishedAt; B2C гидратирует товары batch-запросом.

Raw Product/Post содержат sellerId без вложенного seller: владелец store data — B2B. Seller-facing ответы B2B содержат полный разрешённый seller DTO. Для B2C реализован `POST B2B /internal/stores/batch {ids}` (до 100): возвращает упорядоченные `{id,username,displayName,avatar,bio,joinedAt}` без ownerUserId, доставки и тарифа. B2C обогащает эти данные текущими follower/engagement метриками Users. Сопоставление существующего Users `/masters` и B2B store UUID требует согласования; см. ниже.

Policy: `GET /internal/sellers/{id}/entitlements`; `POST /internal/sellers/entitlements/batch {ids}`; `POST /internal/sellers/{id}/media/validate {url}`. Entitlements:

```json
{"sellerId":"UUID","tier":"free","productLimit":10,"postLimit":5,"onlinePayments":false}
```

Media references для maintenance: `GET /internal/sellers/{id}/media/references` у Goods и Publications возвращает URL-строки всех используемых изображений, включая soft-deleted историю. Эти endpoints не предназначены браузеру.

## Users/Auth — чужой сервис

Конфиг USER_SERVICE_URL. `UserService` Protocol и `UserServiceClient` находятся в `marketplace/b2b/integrations`.

| Метод | Запрос | Ответ data | Статус контракта |
|---|---|---|---|
| GET | `/auth/me`, Cookie: auth-token=JWT | `{user: {id,roles,displayName,username,email,avatar,bio,createdAt}}` | Внешний маршрут согласован API-документом; RS256 issuer/audience/key нужно подтвердить |
| PUT | `/internal/users/{userId}/seller-role`, body `{sellerId}`, Idempotency-Key=store UUID | `{user: ...}` с seller role | **Предложение на согласование**, должно быть идемпотентным и additive для buyer+seller |
| GET | `/internal/sellers/{sellerId}/followers?page=&pageSize=` | `{count,items:[{id,displayName,avatar}],page,pageSize}` | **Предложение**, privacy-safe список; нет email/телефона |

B2B не вызывает публичный become-seller того же gateway, чтобы не образовать рекурсию. Владелец маршрута `POST /users/me/become-seller` в общем gateway должен быть один — B2B оркестратор. Users не создаёт второй магазин. Owner ID — общий user UUID, store ID — отдельный UUID B2B. Нужно согласовать, что follower keys/masters используют именно этот store ID либо явное сопоставление в адаптере.

Dev FakeUsers содержит только две документированные identity, роли в памяти только тестового внешнего адаптера. После перезапуска повторный onboarding восстановит seller role buyer identity; production глобальные роли всегда у Users. Никаких auth/login/register/password endpoint в этом репозитории нет.

## Order/Payment — чужой сервис

ORDER_SERVICE_URL и PAYMENT_SERVICE_URL могут указывать на один сервер.

| Метод | Запрос | Ответ data | Согласование |
|---|---|---|---|
| GET | `/orders?role=seller&sellerId=&status=&q=&page=&pageSize=` | Paginated Order | Основной route/status из API; s2s sellerId, q, scope авторизации подтвердить |
| GET | `/orders/{id}?sellerId=&role=seller` | Order | B2B повторно сверяет sellerId или seller.id |
| PATCH | `/orders/{id}/status?sellerId=`, body `{status,trackingNumber?}`, Idempotency-Key | подтверждённый Order | Владелец выполняет бизнес-переход и возвращает фактическое состояние |
| GET | `/internal/sellers/{id}/orders-summary` | `{newOrdersCount,awaitingShipmentCount,shippedOrdersCount,totalOrdersCount,recentOrders}` | **Предложение**: точные глобальные counts, последние 5; new=created/paid, awaiting=paid |
| POST | `/internal/subscriptions/checkout`, body `{sellerId,tier:"premium",amount:990,currency:"RUB"}` + key | `{reference,paymentUrl,status:"pending"}` | **Предложение**; сумма проверяется платёжным сервисом; key идемпотентен |
| GET | `/internal/subscriptions/{reference}?sellerId=` | `{sellerId,reference,status,tier,validUntil,onlinePayments}` | **Предложение**; status confirmed только после YooKassa; validUntil UTC; onlinePayments подтверждает подключённые платёжные права |

Order DTO сохраняет согласованные поля buyer, seller, items и другие разрешённые Order данные; sellerId может быть выведен из seller.id. Order IDs в URL ограничены буквами, цифрами, `_`, `-`. До отправки команды B2B получает заказ, проверяет владельца и допустимый базовый переход. Сложные fulfillment операции, pickup codes и CDEK/возвраты не реализованы здесь; для их фасада нужен согласованный contract.

`POST /seller/subscription/sync` доступен только продавцу своего магазина, **не принимает proof/tier из тела**: сам читает Payment. Поэтому можно безопасно вызывать после возврата пользователя с платёжной страницы. Для автоматической синхронизации можно использовать авторизованное polling/оркестрацию; webhook платежей принадлежит Payment. Период активности ограничен validUntil, не бесконечен.

FakeOrders возвращает подтверждённый пустой набор. FakePayments создаёт стабильную `.invalid` ссылку и pending; подтверждение переключается только в автоматических тестах, не через публичный dev endpoint. Payment outage никогда не выдаёт Premium.

## Analytics — чужой источник

ANALYTICS_SERVICE_URL: предложен `GET /internal/sellers/{id}/views?from=ISO8601&to=ISO8601` → `{count: nonnegative int}`. Интервал по умолчанию последние 30 дней, максимум 366; даты должны содержать offset/UTC. Отсутствие источника → null и unavailableSections, а не 0. FakeAnalytics явно сообщает демонстрационный 0. Kafka теперь реализована отдельным транспортом для seller/product/publication событий, но не заменяет внешнюю аналитику views. См. [KAFKA_CONTRACT.md](KAFKA_CONTRACT.md); order.status_changed остаётся выключенным предлагаемым контрактом.

## S3

Boto3 S3StorageClient: S3_ENDPOINT, S3_REGION, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, S3_PUBLIC_URL. SDK put/delete работают в thread pool с timeout. Бинарные файлы только в bucket, метаданные в B2B. MinIO локально имеет публичное чтение объектов без листинга bucket, запись только сервером. Production URL предполагает настроенный CDN/public image bucket; закрытые подписанные URL потребуют отдельного согласования, сейчас не используются.

## Проверка контрактов

`tests/test_contracts.py` проверяет URL, query, envelope, передачу cookie/service key/idempotency, parsing полного User и Order, отклонение неверных DTO. Сквозные API-тесты используют настоящие три приложения через httpx ASGITransport. Эти проверки подтверждают реализацию предложенного договора, **не подтверждают совместимость с ещё не подключёнными внешними deployments**.

Экспортируемые схемы: `docs/contracts/{b2b,goods,publications}.openapi.json`. Перед интеграцией команда должна подтвердить отмеченные выше proposals, JWT key/issuer/audience, namespace store/master IDs, маршрутизацию gateway и разрешения s2s. Материалы готовы для передачи, автоматической рассылки нет.
