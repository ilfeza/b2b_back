# API трёх сервисов

Пути ниже включают default `/api`. `/docs`, `/openapi.json`, `/healthz`, `/readyz` находятся в корне каждого приложения. Все бизнес-ответы — `{data: ..., error: null}` или `{data: null, error: {code,message,details,requestId}}`.

B2B: cookie `auth-token` с подтверждённой seller role; исключения: onboarding разрешён buyer, `/plans` публичный. Для изменяющих cookie-запросов нужны `Origin: http://localhost:3000` и `X-CSRF-Protection: 1`. Dev identity явно включается через конфиг. Все `/internal` требуют `X-Service-Key` и предназначены доверенным backend-процессам.

## Полный список маршрутов

`page` ≥1, `pageSize` 1–100; `q` до 200 символов. UUID автоматически валидируются. `*` означает обязательный параметр. Request/response DTO со всеми полями и ограничениями доступны в сохранённых OpenAPI; generic dict используется только для сводок и фасадов.

### b2b

[OpenAPI JSON](contracts/b2b.openapi.json)

| Метод | Путь | Query/headers | JSON / multipart body | Успех |
|---|---|---|---|---|
| POST | `/api/users/me/become-seller` | X-User-Id | StoreCreate | 200 |
| GET | `/api/seller/profile` | X-User-Id | — | 200 |
| PATCH | `/api/seller/profile` | X-User-Id | StorePatch | 200 |
| GET | `/api/seller/delivery` | X-User-Id | — | 200 |
| PATCH | `/api/seller/delivery` | X-User-Id | DeliveryPatch | 200 |
| GET | `/api/seller/onboarding` | X-User-Id | — | 200 |
| GET | `/api/seller/products` | page, pageSize, q, saleMode, X-User-Id | — | 200 |
| POST | `/api/products` | X-User-Id | ProductCreate | 201 |
| GET | `/api/seller/products/{product_id}` | X-User-Id | — | 200 |
| PATCH | `/api/products/{product_id}` | X-User-Id | ProductPatch | 200 |
| DELETE | `/api/products/{product_id}` | X-User-Id | — | 200 |
| GET | `/api/seller/posts` | page, pageSize, q, status, X-User-Id | — | 200 |
| POST | `/api/posts` | X-User-Id | PostCreate | 201 |
| GET | `/api/seller/posts/{post_id}/preview` | X-User-Id | — | 200 |
| GET | `/api/seller/posts/{post_id}` | X-User-Id | — | 200 |
| PATCH | `/api/posts/{post_id}` | X-User-Id | PostPatch | 200 |
| DELETE | `/api/posts/{post_id}` | X-User-Id | — | 200 |
| POST | `/api/seller/posts/{post_id}/publish` | X-User-Id | — | 200 |
| POST | `/api/uploads` | X-User-Id | multipart/form-data: file | 201 |
| GET | `/api/plans` | — | — | 200 |
| GET | `/api/seller/subscription` | X-User-Id | — | 200 |
| POST | `/api/plans/subscribe` | Idempotency-Key*, X-User-Id | Subscribe | 200 |
| POST | `/api/seller/subscription/sync` | X-User-Id | — | 200 |
| GET | `/api/seller/dashboard` | from, to, X-User-Id | — | 200 |
| GET | `/api/seller/followers` | page, pageSize, X-User-Id | — | 200 |
| GET | `/api/seller/orders` | page, pageSize, status, q, X-User-Id | — | 200 |
| GET | `/api/seller/orders/{order_id}` | X-User-Id | — | 200 |
| PATCH | `/api/seller/orders/{order_id}/status` | Idempotency-Key*, X-User-Id | StatusChange | 200 |
| GET | `/api/internal/sellers/{seller_id}/entitlements` | — | — | 200 |
| POST | `/api/internal/sellers/{seller_id}/media/validate` | — | MediaCheck | 200 |
| POST | `/api/internal/sellers/entitlements/batch` | — | SellerBatch | 200 |
| POST | `/api/internal/stores/batch` | — | SellerBatch | 200 |
| GET | `/healthz` | — | — | 200 |
| GET | `/readyz` | — | — | 200 |

### goods

[OpenAPI JSON](contracts/goods.openapi.json)

| Метод | Путь | Query/headers | JSON / multipart body | Успех |
|---|---|---|---|---|
| GET | `/api/internal/sellers/{seller_id}/products` | page, pageSize, q, saleMode, delivery | — | 200 |
| POST | `/api/internal/sellers/{seller_id}/products` | — | ProductCreate | 201 |
| POST | `/api/internal/sellers/{seller_id}/products/batch` | — | Batch | 200 |
| GET | `/api/internal/sellers/{seller_id}/products-summary` | threshold | — | 200 |
| GET | `/api/internal/sellers/{seller_id}/products/{product_id}` | — | — | 200 |
| PATCH | `/api/internal/sellers/{seller_id}/products/{product_id}` | — | ProductPatch | 200 |
| DELETE | `/api/internal/sellers/{seller_id}/products/{product_id}` | — | — | 200 |
| GET | `/api/internal/products` | page, pageSize, q, sellerId, saleMode, delivery | — | 200 |
| GET | `/api/internal/products/{product_id}` | — | — | 200 |
| GET | `/api/internal/sellers/{seller_id}/media/references` | — | — | 200 |
| POST | `/api/internal/products/batch` | — | Batch | 200 |
| GET | `/healthz` | — | — | 200 |
| GET | `/readyz` | — | — | 200 |

### publications

[OpenAPI JSON](contracts/publications.openapi.json)

| Метод | Путь | Query/headers | JSON / multipart body | Успех |
|---|---|---|---|---|
| GET | `/api/internal/sellers/{seller_id}/posts` | page, pageSize, q, status | — | 200 |
| POST | `/api/internal/sellers/{seller_id}/posts` | — | PostCreate | 201 |
| GET | `/api/internal/sellers/{seller_id}/posts/{post_id}` | — | — | 200 |
| PATCH | `/api/internal/sellers/{seller_id}/posts/{post_id}` | — | PostPatch | 200 |
| DELETE | `/api/internal/sellers/{seller_id}/posts/{post_id}` | — | — | 200 |
| POST | `/api/internal/sellers/{seller_id}/posts/{post_id}/publish` | — | — | 200 |
| GET | `/api/internal/posts` | page, pageSize, q, sellerId | — | 200 |
| GET | `/api/internal/posts/{post_id}` | — | — | 200 |
| GET | `/api/internal/sellers/{seller_id}/media/references` | — | — | 200 |
| GET | `/healthz` | — | — | 200 |
| GET | `/readyz` | — | — | 200 |

## JSON и валидация

Onboarding:

```json
{"username":"ceramics_shop","displayName":"Керамика Анны","bio":"Ручная лепка"}
```

Ответ data: `{user: подтверждённый профиль Users с roles, seller: профиль магазина}`. Повтор возвращает тот же магазин; username неизменяем. PATCH профиля принимает только `displayName`, `bio`, `avatar`; явный null обязательных строк и неизвестные поля дают 400.

Профиль содержит `publicUrl: "/shops/{username}"` — реальный маршрут предоставленного frontend. Префикс настраивается через `PUBLIC_STOREFRONT_PREFIX`; это ссылка на страницу, а не отдельный API endpoint. Покупательский frontend должен получать данные магазина через свой B2C backend.

Товар:

```json
{"title":"Чашка","description":"Ручная работа","image":"URL из uploads","price":1800,"saleMode":"deal_only","availableDeliveries":["pickup"],"pickupPoints":[{"city":"Москва","address":"Адрес 1"}],"dealContacts":"Свяжитесь с мастером","inStock":3}
```

`price` и `inStock` — строгие неотрицательные целые, максимум 2147483647. saleMode: online/out_of_stock/deal_only; delivery: pickup/cdek без повторов. Pickup требует адрес; deal_only требует контакты; online требует stock>0, доставку и подтверждённые Premium/payment права. Image принимает URL своего ранее загруженного изображения.

Product data: поля запроса + UUID `id`, `sellerId`, `currency: RUB`, `createdAt`, `updatedAt`, `deletedAt`, `purchasable`. B2B добавляет `seller`. PATCH — те же разрешённые поля частично; объединённый результат проверяется целиком, null обязательных полей отклоняется. DELETE возвращает data=null. Список: `{items,total,page,pageSize}`; поиск по названию, фильтр saleMode; внутренний каталог также delivery/sellerId. Глобальный saleMode-фильтр требует sellerId, чтобы учитывать действующий тариф.

Публикация:

```json
{"title":"Как создаётся чашка","body":"<h2>Мастерская</h2><p>Ручная лепка</p>","coverImage":"URL из uploads","productIds":["UUID"],"status":"draft"}
```

Draft может быть незавершённым. Для published нужны заголовок и текст после sanitization; productIds уникальны, максимум 100, порядок сохраняется. CoverImage необязателен. PATCH допускает частичные title/body/coverImage/status/productIds; coverImage=null убирает обложку. Post data добавляет id/sellerId/timestamps/publishedAt. B2B добавляет seller, упорядоченные products и nullable likesCount/commentsCount. В seller context isLiked=false; покупательскую персонализацию делает B2C. При отказе product hydration products=[] и unavailableSections=["products"]. Внутренний общий read API никогда не возвращает drafts.

Delivery PATCH:

```json
{"pickupEnabled":true,"pickupPoints":[{"city":"Москва","address":"Адрес 1"}],"cdekEnabled":false}
```

Поля PATCH опциональны; сохраняется только валидный объединённый результат. При pickupEnabled=true обязателен непустой массив валидных city/address.

Upload: multipart поле `file`; JPEG/PNG/WebP, default до 5 MiB, до 20 млн пикселей. Ответ data: `{id,url,contentType,size}`. Формат проверяется по декодированному содержимому, MIME должен совпадать; имя клиента игнорируется.

Подписка: `POST /plans/subscribe {tier:"premium"}` с обязательным `Idempotency-Key`. Ответ `{reference,paymentUrl,status}`; pending не даёт Premium. `/seller/subscription/sync` не принимает тариф/подтверждение от пользователя — запрашивает Payment сам. `/seller/subscription` возвращает действующие tier/limits/onlinePayments.

Статус заказа: `{status:"shipped",trackingNumber:"CDEK123"}` и Idempotency-Key. Допустимы paid→shipped, shipped→delivered, created/paid→cancelled. Возвращается фактический Order от внешнего сервиса с проверкой seller.

Dashboard: newOrdersCount, awaitingShipmentCount, shippedOrdersCount, totalOrdersCount, totalProductsCount, lowStockProducts, recentOrders, followersCount, productViewsCount, period, unavailableSections, quickActions, demoData. Недоступные секции — null, подтверждённый пустой источник — 0/[]; период задаётся `from`/`to` с timezone, default 30 дней.

## Коды ответа

| HTTP | code | Значение |
|---|---|---|
| 200 | — | Получение/изменение/удаление/идемпотентный onboarding |
| 201 | — | Товар, пост, upload созданы |
| 400 | VALIDATION_ERROR | Невалидные DTO, поля, связи, delivery, файл, переход |
| 401 | UNAUTHORIZED | Нет/невалидная/просроченная cookie; неверный internal key |
| 403 | FORBIDDEN | Нет seller role/store/CSRF, нет online payment права |
| 403 | QUOTA_EXCEEDED | Превышен лимит; details.limit, когда применимо |
| 404 | NOT_FOUND | Отсутствующий, чужой или удалённый seller-owned ресурс; draft в общем read API |
| 409 | CONFLICT | Username занят, нарушение UNIQUE, другой checkout уже pending/active |
| 405 | METHOD_NOT_ALLOWED | Неизвестный метод |
| 500 | SERVER_ERROR | Безопасное описание внутреннего сбоя |
| 502 | UPSTREAM_ERROR | Невалидный DTO/подтверждение upstream |
| 503 | DEPENDENCY_UNAVAILABLE | Timeout, недоступные БД/storage/сервис |

Ошибки валидации FastAPI нормализуются в 400; OpenAPI отражает этот обработчик и единый error envelope. Request ID есть в X-Request-Id и error.requestId. Логи и сообщения не отражают секреты upstream.

## Расширения согласованного API

На каждом сервисе добавлены `GET /api/internal/events?limit=20` (outbox counts, receipts, activity, quarantine) и `GET /api/internal/events/outbox/{event_id}` (event envelope). Требуют `X-Service-Key`, не предназначены браузерному frontend. Kafka topics/payloads и operational поля описаны в [KAFKA_CONTRACT.md](KAFKA_CONTRACT.md). [JSON Schema событий](contracts/events.v1.schema.json).

Добавлены: `/seller/onboarding`; `/seller/products/{id}`; `/seller/posts/{id}` и `/preview`, `/publish`; `/seller/subscription` и `/sync`; `/seller/followers`; `/uploads` обязательный по SRS; `/seller/orders` с details/status вместо перехвата общих Order routes; все `/internal/...` APIs. Поля status/productIds/publishedAt/updatedAt/deletedAt/purchasable/availability и nullable недоступные метрики также документированные расширения.

POST /posts по умолчанию создаёт draft. Это уточнение поведения требует frontend передавать status=published или вызывать publish action при нажатии «Опубликовать». IDs создаются как UUID; никакие `p1`/`s1` из иллюстраций не служат реальными ID.
