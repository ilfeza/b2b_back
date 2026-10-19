# Сверка B2B с исходным API-контрактом — 19 октября 2026

**Вывод: полного совпадения с исходным контрактом нет.** Основные операции реализованы, однако старый клиент без изменений не сможет вызвать ряд маршрутов и изменяющих запросов; некоторые типы ответов и значения также отличаются. Успешные автоматические тесты подтверждают текущую реализацию, а не буквальное соответствие исходному DOCX.

Основание: [исходный DOCX](<API-контракт и модель данных.docx>), [извлечённый текст](<source_text/API-контракт и модель данных.txt>), фактический OpenAPI и ответы работающего B2B перед остановкой Docker. Дополнительно проверены Python routes, DTO и service logic. Автоматический список методов/путей: [b2b-route-comparison.json](test-results/b2b-route-comparison.json). Снятые ответы: [b2b-contract-observed.json](test-results/b2b-contract-observed.json). [OpenAPI работающего приложения](test-results/b2b.runtime.openapi.json) совпал с сохранённым `contracts/b2b.openapi.json`.

При оценке учитывалось более позднее указание пользователя: Goods и Publications — наши отдельные сервисы, Users/Auth и Order/Payment — сервисы других команд. Отсутствие чужих сервисов внутри B2B само по себе не является дефектом. При этом перенос публичного URL всё равно требует согласования с frontend/gateway; внутренние решения не изменяют исходный контракт автоматически.

## 1. Сверка маршрутов, используемых B2B

Проверены 21 маршрут, используемый кабинетом по исходному контракту, и опциональный upload. У 17 совпадают метод и путь; у 5 есть замена с другим путём. Это подсчёт маршрутов, а не процент совместимости: совпавший путь ещё может отличаться заголовками, DTO или поведением.

| Раздел контракта | Ожидаемый метод и путь без `/api` | Текущая реализация | Результат |
|---|---|---|---|
| 4.2 | POST `/users/me/become-seller` | Тот же | Путь и 200 совпадают; см. замечание о User DTO |
| 5.7 | GET `/seller/profile` | Тот же | Путь совпадает; nullable метрики |
| 5.8 | PATCH `/seller/profile` | Тот же | Путь совпадает; ограничение avatar своим upload |
| 5.9 | GET `/seller/delivery` | Тот же | Основной DTO совпадает |
| 5.10 | PATCH `/seller/delivery` | Тот же | DTO совпадает; дополнительно разрешён частичный PATCH |
| 6.2, таблица edit-page | GET `/products/{id}` | GET `/seller/products/{product_id}` | Другой путь; исходный GET даёт 405 даже для существующего товара |
| 6.3 | POST `/products` | Тот же | 201 совпадает; media/Free/error rules отличаются |
| 6.4 | PATCH `/products/{id}` | Тот же | Ошибки владения и разрешённые поля отличаются |
| 6.5 | DELETE `/products/{id}` | Тот же | 200 и `{data:null,error:null}` совпадают; soft delete |
| 6.6 | GET `/seller/products` | Тот же | q/saleMode/page/pageSize и pagination совпадают |
| 7.2, таблица edit-page | GET `/posts/{id}` | GET `/seller/posts/{post_id}` | Другой путь; исходный GET даёт 405 даже для существующего поста |
| 7.3 | POST `/posts` | Тот же | 201 совпадает; default draft и метрики отличаются |
| 7.4 | PATCH `/posts/{id}` | Тот же | Вместо буквального Partial<Post> — явный PostPatch |
| 7.5 | DELETE `/posts/{id}` | Тот же | 200 и envelope совпадают; soft delete |
| 7.8 | GET `/seller/posts` | Тот же | Пагинация совпадает; добавлен status, список включает drafts |
| 8.2 | GET `/orders?role=seller` | GET `/seller/orders` | Другой путь; исходный GET даёт 404 |
| 8.3 | GET `/orders/{id}` | GET `/seller/orders/{order_id}` | Другой путь; только seller context |
| 8.4 | PATCH `/orders/{id}/status` | PATCH `/seller/orders/{order_id}/status` | Другой путь и обязательный Idempotency-Key |
| 10.1 | GET `/plans` | Тот же | Форма ответа и цены совпадают; набор features отличается |
| 10.2 | POST `/plans/subscribe` | Тот же | Добавлены обязательный Idempotency-Key и отдельный sync |
| 11.1 | GET `/seller/dashboard` | Тот же | Основные ключи есть; nullable при отказах, дополнительные поля |
| 12, опционально | POST `/uploads` | Тот же | Multipart реализован; data содержит url и дополнительные metadata |

Routes: [marketplace/b2b/api/routes.py](../marketplace/b2b/api/routes.py). B2B вызывает Goods/Publications по HTTP, без дублирования их хранения.

## 2. Отличия, которые влияют на клиент

### A1. Заказы перенесены под `/seller/orders`

Контракт 8.2–8.4 и таблица соответствия страниц используют `/orders`, `/orders/{id}`, `/orders/{id}/status`. У B2B этих маршрутов нет; есть `/seller/orders...`. Upstream-клиент действительно обращается к Order Service по `/orders`, но это не делает старые пути доступными frontend через B2B.

Причина решения — изоляция seller facade и отсутствие дублирующего Order backend. Для совместимости нужно согласовать маршрутизацию gateway или новый контракт frontend. GET `/api/orders?role=seller` фактически вернул **404**. Исходные canonical URL могут обслуживаться чужим Order Service, но это не проверено на общем стенде.

### A2. Детали товара и поста читаются по другому пути

Исходные страницы редактирования используют GET `/products/{id}` и GET `/posts/{id}`. Текущий B2B предоставляет приватные `/seller/products/{id}` и `/seller/posts/{id}`. Исходные GET возвращают **405**, потому что такой путь существует для PATCH/DELETE, но не для GET. Проверено и с реальными ID seed.

Приватный endpoint поста нужен для собственного черновика; публичный B2C read не должен раскрывать drafts. Нужно явно обновить привязку страниц к endpoints, сохранив публичные reads в ответственности B2C.

### A3. Новые обязательные заголовки

- Контракт описывает JWT cookie и CORS `Content-Type`. Для cookie-мутаций реализация дополнительно требует разрешённый `Origin` и **`X-CSRF-Protection: 1`**. Без них авторизованный запрос получает 403. Это разумная защита, но клиент из исходного контракта её не отправляет. Заголовок CSRF не объявлен отдельным параметром операции в текущем OpenAPI.
- POST `/plans/subscribe` и PATCH seller order status требуют **`Idempotency-Key`**, хотя в исходном документе его нет. Без него subscribe фактически вернул **400**. Значение — 1–100 символов `[a-zA-Z0-9_-]`.
- `X-User-Id` — только явно включённая dev-авторизация для локальной проверки. Это не замена JWT и не production-контракт. Cookie в production по-прежнему `auth-token`; её выпускает внешний Auth.

Источники: [security.py](../marketplace/shared/security.py), [routes.py](../marketplace/b2b/api/routes.py), [app.py](../marketplace/shared/app.py).

### A4. POST `/posts` без status создаёт draft

В контракте 7.3 нет поля status и описано обычное создание поста; отдельный draft lifecycle там не определён. Сейчас `status` по умолчанию **draft**, а title/body могут быть пустыми. Запрос старой кнопки «Опубликовать» получит 201, но запись не будет опубликована.

Для публикации надо передать `status:"published"` или вызвать POST `/seller/posts/{id}/publish`. Сама поддержка draft/publish обязательна по позднему указанию пользователя и есть в Project Management Report; **выбор draft по умолчанию требует фиксации в API-контракте**. Также GET `/seller/posts` без фильтра включает drafts.

Источник: [PostCreate / PostPatch](../marketplace/publications/schemas/posts.py), [PostsService](../marketplace/publications/services/posts.py).

### A5. Числовые метрики стали nullable

В контракте Seller.rating/followersCount/productsCount/postsCount и Post.likesCount/commentsCount имеют тип `number`. Сейчас:

- rating всегда `null`: источник рейтинга не подключён;
- likesCount/commentsCount всегда `null`, включая только что созданный пост; контракт 7.3 обещает первоначальные **0**;
- остальные агрегаты и dashboard counters могут быть `null` при недоступной зависимости, с `unavailableSections`.

Это несовместимо с исходными строгими TS-типами и вызовами вроде `rating.toFixed(1)`. Нужны либо настоящие источники значений, либо согласованные nullable DTO. Подставлять 0 вместо неизвестного рейтинга или неизвестного количества нельзя считать подтверждёнными данными.

Источник: [SellerFacade](../marketplace/b2b/services/facade.py), [response DTO](../marketplace/b2b/schemas/responses.py). `isFollowing=false` и `isLiked=false` выставляются в seller context; покупательская персонализация этим фасадом не реализована.

### A6. Произвольная URL-картинка больше не принимается

Контракт 1.4/6.3/7.3/12 разрешает URL строкой и говорит, что для MVP пользователь вставляет ссылку, upload необязателен. Реализация принимает привязку image/avatar/coverImage только к объекту, ранее загруженному **этим магазином** через `/uploads`. Посторонняя ссылка даёт 400.

Для реальной интеграции сначала multipart upload, затем использование выданного url. Это дополнительное ограничение принадлежности media, которое не записано в исходном контракте. CoverImage можно убрать через null; avatar сбрасывается пустой строкой, а не null.

Источник: [StoreService.validate_media / update](../marketplace/b2b/services/stores.py), [CatalogService.validate](../marketplace/goods/services/catalog.py), [PostsService.validate](../marketplace/publications/services/posts.py).

### A7. Коды ошибок не везде совпадают

| Ситуация | Исходный контракт | Сейчас |
|---|---|---|
| Лимит Free: товар или пост | 403 `FORBIDDEN` | 403 **`QUOTA_EXCEEDED`**, может содержать details.limit |
| PATCH чужого товара, 6.4 | 403 `FORBIDDEN` | **404 `NOT_FOUND`**, ресурс скрывается как отсутствующий |
| Чужой productId в новой публикации, 7.3 | 403 `FORBIDDEN` | **400 `VALIDATION_ERROR`**, сообщение объединяет отсутствующий/удалённый/чужой товар |
| Формат деталей validation | Пример `{field:"title"}` | Обычно `{errors:[{field:"body.title",message:...}]}`; отдельные domain errors используют `{field:...}` |
| Отказ upstream | Не перечислен отдельно | 502 `UPSTREAM_ERROR`, 503 `DEPENDENCY_UNAVAILABLE` |

RequestId в ошибке — дополнительное поле. Защита от раскрытия чужих UUID допустима как решение, но нельзя считать её буквальным совпадением ожидаемых 403. Клиенту нужно обрабатывать новую таблицу кодов и оба формата details.

Источники: [repositories.py](../marketplace/shared/repositories.py), [errors.py](../marketplace/shared/errors.py), Goods/Publications services выше. Изоляция продавцов и ошибки владения проверены существующими security/publications тестами.

### A8. Тарифные возможности отличаются от ответа 10.1

Структура `{tier,name,price,features}` и цены Free=0 / Premium=990 совпадают. Но в исходном JSON Premium имеет аналитику и приоритет `included:true`, комиссии 10%/5% присутствуют. В реализации аналитика/приоритет помечены **не реализованными и included:false**, комиссий в features нет; добавлены продажи по договорённости/online rights.

Project Management Report прямо исключает построение Premium analytics/search boost из текущего MVP. Поэтому старую feature matrix нужно привести в соответствие согласованному объёму, а не просто объявить неработающие возможности доступными.

Источник: [SubscriptionService.plans](../marketplace/b2b/services/subscriptions.py); PM report, извлечённый текст, строки 248–251.

### A9. Подтверждение Premium требует отдельного sync

Контракт 10.2: redirect на paymentUrl, после webhook backend обновляет planTier. Сейчас subscribe возвращает paymentUrl **плюс reference/status**; pending не даёт Premium. B2B меняет тариф только после POST `/seller/subscription/sync`, когда сам получает доверенный статус от Payment Service.

Webhook принадлежит Order/Payment по границе ответственности — создавать его дубликат в B2B не нужно. Но автоматический запуск sync после подтверждения оплаты **не подключён**: без вызова sync или внешней оркестрации оплата сама не обновит локальный planTier. Dev payment URL ведёт на `.invalid` и никогда не подтверждает реальный платёж.

Источник: [SubscriptionService.initiate / sync](../marketplace/b2b/services/subscriptions.py), [внешние клиенты](../marketplace/b2b/integrations/external.py). Требуется договорённость с Payment, а не новый платёжный сервис.

### A10. Order/User DTO не гарантируют все обязательные поля контракта

Исходный Order требует buyer, seller, delivery и вложенные items.product. Внешний `Order` в B2B требует только id/sellerId/status/createdAt/items/total; items — `list[dict]`. Дополнительные buyer/seller/delivery **сохраняются**, если пришли, но их отсутствие не признаётся ошибкой upstream. Поэтому строгий Order для frontend не гарантирован. Списки и details в OpenAPI объявлены generic dict.

Аналогично onboarding возвращает `Identity` вместо строгого User: username/avatar/email имеют пустые defaults, createdAt допускает null. Полный корректный User от реального Users сохранит контрактные поля, но некорректный upstream с недостающими полями не будет отвергнут. В dev профиль пользователя намеренно неполный.

Нужна строгая модель на границе внешнего адаптера после согласования Order/User. Это отдельный недостаток проверки wire contract; реальные сервисы других команд не тестировались.

Источники: [Order](../marketplace/b2b/integrations/contracts.py), [Identity](../marketplace/shared/schemas.py), [external.py](../marketplace/b2b/integrations/external.py).

### A11. PATCH описан в исходнике шире, чем принимается

- 6.4 говорит `Partial<Product>` без id/sellerId/seller/createdAt. Буквально это допускает currency, однако текущий ProductPatch **не принимает currency**, даже `"RUB"`: дополнительные поля запрещены. Должен быть явный список редактируемых полей.
- 7.4 говорит `Partial<Post>`, но реализация принимает title/body/coverImage/**productIds/status**. Поле `products` из модели Post, как и `isLiked`, не редактируется через этот PATCH. Счётчики/персонализация должны оставаться read-only; поправить нужно неоднозначную формулировку контракта.
- В OpenAPI многие PATCH-поля имеют `anyOf [...,null]`, хотя явный null для обязательных данных после merge даёт 400. Например, ProductPatch.title/price/inStock, PostPatch.title/body/status/productIds, StorePatch.displayName, DeliveryPatch.pickupEnabled. Это расхождение **документации OpenAPI и поведения**; omit и null сейчас показываются менее точно, чем валидируются.

Источники: [ProductPatch](../marketplace/goods/schemas/products.py), [PostPatch](../marketplace/publications/schemas/posts.py), [Store/Delivery Patch](../marketplace/b2b/schemas/store.py).

### A12. OpenAPI неполно описывает несколько успешных ответов

Onboarding, profile, delivery, plans, dashboard, orders и subscription используют `Envelope[dict]` / `Envelope[list[dict]]`. Swagger видит произвольный объект, а не контрактные поля и required/nullability. Это не мешает ручному запросу, но из такого OpenAPI нельзя получить полноценный typed SDK для этих ответов. ProductView/PostView описаны существенно строже.

Источник: [routes.py](../marketplace/b2b/api/routes.py), [сохранённый OpenAPI](contracts/b2b.openapi.json). Совпадение сохранённого и runtime OpenAPI не означает совпадения с DOCX: они оба описывают текущую реализацию.

## 3. Дополнения и уточнения, которые тоже нужно записать

| Область | Реализация / что нужно согласовать |
|---|---|
| IDs | Product/Post/Seller принимают только UUID. Контракт допускает UUID **или slug** и приводит p1/s1. Mock IDs p1/s1 не принимаются; невалидный UUID даёт 400. Order ID отдельно допускает `[a-zA-Z0-9_-]`, до 100 символов. |
| Дополнительная валидация | q≤200; pageSize≤100; title/displayName≤200; username `[a-z][a-z0-9_]{2,31}`; stock/price≤2147483647; pickupPoints≤30; productIds≤100, без повторов; неизвестные JSON-поля запрещены. Большинство границ отсутствует в исходном документе. |
| Free и online | Free допускает deal_only/out_of_stock, online требует Premium и подтверждённых платёжных прав, stock>0 и доставку. Более строгие правила соответствуют PM report, но в продуктовом разделе API-контракта не расписаны. |
| Поля Product/Post | Дополнительно updatedAt, deletedAt/purchasable у товаров; status/productIds/publishedAt/updatedAt у постов. Нейтральны для клиента, который игнорирует неизвестные поля. |
| Nullable optional | coverImage/dealContacts могут явно быть null; в TS-моделях исходника написано `?:string` без null, хотя пример товара уже содержит dealContacts:null. Документ противоречив; nullable следует записать явно. |
| Удаление | Soft delete с tombstone; старые связи публикаций с удалёнными товарами сохраняются. В ответе такой товар недоступен для покупки; новый link на него запрещён. HTTP DELETE 200 соответствует контракту. |
| Профиль | Дополнительные ownerUserId/createdAt/publicUrl/unavailableSections. publicUrl=`/shops/{username}`; таблица страниц исходного контракта указывает `/seller/[username]`. Это frontend route, не новый backend endpoint; префикс настраивается. |
| Dashboard | Основные 6 ключей 11.1 присутствуют. Добавлены awaitingShipmentCount/followersCount/productViewsCount/period/quickActions/demoData/unavailableSections; при отказах секции nullable. from/to в текущем коде передаются аналитике views, а summary заказов остаётся без периода. Нельзя трактовать весь dashboard как отфильтрованный по этим датам. |
| Дополнительные маршруты | `/seller/onboarding`, private product/post details, preview/publish, subscription/get+sync, followers и internal APIs отсутствуют в исходном документе. Их следует оформить как расширения. |

## 4. Что совпадает и что относится к другим командам

Совпадают base URL `/api`, JWT cookie name, camelCase, integer RUB price, основные enums, `{data,error}`, page/pageSize pagination, методы CRUD и create 201/delete 200, проверка seller, базовый onboarding/delivery, HTML sanitization с согласованными тегами. Переходы заказов created/paid→cancelled, paid→shipped, shipped→delivered соответствуют 8.4; несовпадение касается маршрута и обязательного заголовка.

Отсутствие в B2B login/logout/register, buyer profile, public feed/catalog, follow/like actions, buyer checkout, создания заказов и YooKassa webhook **не записывается в дефекты B2B**: это другие владельцы по последнему указанию пользователя. Для подключения готовы клиенты и предложенные internal contracts в [INTEGRATIONS.md](INTEGRATIONS.md). При этом production интеграция с ними пока не подтверждена.

## 5. Приоритет согласования

1. Зафиксировать canonical routes/gateway для заказов и private edit reads; добавить в frontend CSRF и Idempotency-Key.
2. Согласовать default draft/publish, обязательный upload и nullable метрики: сейчас старые формы/типы несовместимы.
3. Уточнить таблицу ошибок, feature matrix и механизм вызова subscription sync после Payment confirmation.
4. Сделать строгими User/Order DTO и ответы OpenAPI; исправить документирование PATCH omit/null и списков редактируемых полей.

Это результат аудита. Бизнес-логика и публичные маршруты в ходе этой проверки не менялись; контракт других команд не объявляется согласованным.
