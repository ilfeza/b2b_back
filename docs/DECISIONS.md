# Решения и расхождения

Приоритет: уточнение пользователя → остальные явные указания пользователя → функциональные требования SRS → согласованный API-контракт → уточнения MVP Project Management Report.

| Вопрос | Решение |
|---|---|
| Владение Goods/Publications | Уточнение пользователя приоритетно: оба полноценные сервисы этой команды, независимо запускаемые FastAPI, отдельные схемы/миграции/тесты. B2B вызывает их через typed HTTP, не хранит товары/посты. |
| Kotlin/Spring, Kubernetes, Redis из SRS | Python 3.12/FastAPI и Docker Compose по заданию. Redis, рекомендации и покупательские page assemblers не входят в эти сервисы. |
| `/api/v1` SRS vs `/api` API-контракта | По умолчанию `/api`, один API_PREFIX. `/healthz`, `/readyz`, `/docs`, `/openapi.json` вне префикса. Настройка `/api/v1` допустима согласованно с gateway и всеми service base URLs. |
| JWT Bearer в SRS vs HttpOnly cookie в контракте | Cookie `auth-token`. RS256 public key + issuer/audience/exp/sub, затем подтверждённый user от Auth. Cookie выпускает только Auth. Для мутаций mandatory Origin + custom CSRF header. |
| Названия seller endpoints в SRS предварительные | Внешние routes из API-контракта сохранены. Добавления details/preview/publish/onboarding/followers/subscription/sync явно перечислены в API.md. |
| Публичная ссылка магазина | `/shops/{username}` по фактическому frontend HandmadeGoodsPlatform commit ad00584; `PUBLIC_STOREFRONT_PREFIX` настраивает путь. `/seller` остаётся кабинетом. |
| Order routes общие | B2B использует `/seller/orders/...`; upstream сопоставляется с `/orders?role=seller`. POST /orders, YooKassa webhook и checkout не реализуются. |
| SRS seller order cancellation из shipped vs API-контракт до shipped | Разрешены created/paid→cancelled, paid→shipped, shipped→delivered. Order остаётся владельцем фактических переходов. Pickup-код/stock-confirmation не придумываются без согласованного контракта. |
| Post body plain textarea в старом SRS vs HTML в обновлённом контракте | HTML сохраняется только после очистки. Теги p,h1-h3,strong,em,s,ul,ol,li,blockquote,br; атрибуты запрещены. |
| Draft optional в старом UI | Поддержаны как обязательная часть уточнённой задачи. POST /posts без status создаёт draft; status=published или publish action публикует. Draft может быть пустым. |
| Upload optional в контракте | По SRS обязателен. Реальный multipart S3 upload, Pillow decode/reencode, metadata в B2B, проверка владельца при привязке. |
| URL картинок из примеров контракта | Поле остаётся URL строкой; разрешены только URLs ранее загруженных этим seller объектов. Это предотвращает привязку чужого media через URL. |
| Free лимит 10–15 в SRS | MVP: 10 товаров, 5 записей; конфигурируемо. Все неудалённые товары и записи (включая drafts) считаются. Out_of_stock входит в лимит. |
| Free продажи | deal_only + contacts; out_of_stock допустим как недоступный товар. online запрещён. Для Premium online дополнительно нужны подтверждённые платёжные права, положительный stock и доставка. |
| Downgrade/expiry | Лишние существующие объекты сохраняются, новые создания запрещены. Онлайн-продажи немедленно недоступны при проверке истёкших прав. Старые публикации остаются видны; перепубликация при превышении квоты запрещена. |
| Подписка 990 ₽ | PREMIUM_PRICE, default 990. B2B только инициирует Payment и проверяет доверенный статус; нет самостоятельного webhook оплаты или выдачи Premium по JSON. |
| Premium analytics/search boost/commission | Аналитика и продвижение явно помечены не реализованными; ставки комиссий не объявлены работающими, расчёт платежей принадлежит Order/Payment. |
| Удаление товаров и ссылки | Soft delete с историческим tombstone, сохранение post_products, purchasable=false. Создание новых ссылок на удалённый товар запрещено. |
| Foreign keys между сервисами | Только локальные FK. Seller/product external UUID между схемами проверяются HTTP; нельзя связывать схемы FK и делать прямые JOIN. |
| pickup_points отдельная таблица vs модель настроек | Ограниченный массив DTO city/address в JSON поля своей строки настроек/товара. Атомарный PATCH, до 30 пунктов; отдельная сущность и CRUD пунктов MVP не нужны. |
| Отсутствующие rating, views, likes/comments | Не придумываются: null и availability для недоступных метрик; в явно включённых dev адаптерах известный пустой набор даёт 0. isFollowing/isLiked false в seller-owned контексте; персонализацию покупателя делает B2C. |
| Модерация 5.2.1–06 vs отсутствие admin/moderation MVP | Административной очереди и автоматической модерации нет согласно явной границе задачи и PM report. Published не означает прохождение модерации. Это незакрытая часть общего SRS вне ответственности данного MVP. |
| Kafka | Позднее обязательное указание пользователя приоритетно: реализованы KRaft, aiokafka, outbox/inbox, consumers и DLQ для семи предварительных событий. Topics/payloads документированы как предлагаемые в KAFKA_CONTRACT.md; внешнее order.status_changed выключено до согласования. Права и квоты — актуальные HTTP/DB проверки. |
| Недоступные образы MinIO/mc | MinIO собирается из официального исходного релиза RELEASE.2025-04-22T22-12-26Z; bucket и public-read policy создаёт scripts/minio_init.py через boto3. При реальном Docker-запуске исходные Docker Hub образы были недоступны, Quay вернул 401. |
| Производительность 100 concurrent / p95 | Есть воспроизводимый smoke. SLA и пилотная нагрузка не подтверждены без отдельного измерения. |
| Внешние контракты Users/Payment/Analytics | Предлагаемые internal endpoints изолированы адаптерами, опубликованы в INTEGRATIONS.md и contract tests; ещё требуют согласования. Никакие реальные внешние сервисы не считаются протестированными по результатам fake/mock. |
| Shared service key | Минимальная доверенная backend-сеть MVP. Не выдаётся клиентам; production secret ≥32 chars и защищённый канал. Более тонкие scopes/read credentials/mTLS — изменение контракта безопасности при подключении недоверенных consumers. |

Миграции проверяют существование схемы через pg_namespace перед CREATE. Compose заранее создаёт схему с нужным владельцем; прикладным ролям не выдаётся CREATE на всей БД. COMMIT выполняется в dependency с scope=function до отправки успешного HTTP-ответа.
