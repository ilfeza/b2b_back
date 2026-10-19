from __future__ import annotations

import asyncio
from datetime import timedelta

from marketplace.b2b.services.stores import StoreService
from marketplace.shared.db import utcnow
from marketplace.shared.errors import DomainError, forbidden, invalid


class SellerFacade:
    def __init__(self, db, state):
        self.db, self.state = db, state
        self.stores = StoreService(db, state)

    async def snapshot(self, store):
        data = {
            "id": str(store.id),
            "username": store.username,
            "displayName": store.display_name,
            "bio": store.bio,
            "avatar": store.avatar,
            "joinedAt": store.created_at,
            "isFollowing": False,
            "rating": None,
            "followersCount": None,
            "productsCount": None,
            "postsCount": None,
        }
        results = await asyncio.gather(
            self.state.users.followers(store.id, 1, 1),
            self.state.goods.summary(store.id, self.state.settings.low_stock_threshold),
            self.state.publications.list(store.id, page=1, pageSize=1),
            return_exceptions=True,
        )
        unavailable = []
        for source, result in zip(("followers", "products", "posts"), results, strict=True):
            if isinstance(result, DomainError):
                unavailable.append(source)
            elif isinstance(result, BaseException):
                raise result
            elif source == "followers":
                data["followersCount"] = result.count
            elif source == "products":
                data["productsCount"] = result["totalProductsCount"]
            else:
                data["postsCount"] = result.total
        data["unavailableSections"] = unavailable
        return data

    async def profile(self, store):
        data = await self.snapshot(store)
        policy = await self.stores.entitlement(store.id)
        data.update(
            ownerUserId=str(store.owner_user_id),
            createdAt=store.created_at,
            planTier=policy.tier,
            delivery=await self.stores.delivery(store),
            publicUrl=f"{self.state.settings.public_storefront_prefix.rstrip('/')}/{store.username}",
        )
        return data

    async def products(self, store, result):
        seller = await self.snapshot(store)
        if hasattr(result, "items") and not isinstance(result, dict):
            data = result.model_dump(mode="json", by_alias=True)
            for product in data["items"]:
                product["seller"] = seller
            return data
        data = result.model_dump(mode="json", by_alias=True)
        data["seller"] = seller
        return data

    async def posts(self, store, result):
        seller = await self.snapshot(store)
        page = hasattr(result, "items") and not isinstance(result, dict)
        posts = result.items if page else [result]
        ids = list(dict.fromkeys(i for p in posts for i in p.product_ids))
        unavailable_sections = []
        try:
            batches = await asyncio.gather(
                *(
                    self.state.goods.batch(store.id, ids[start : start + 100], include_deleted=True)
                    for start in range(0, len(ids), 100)
                )
            )
            products = [product for batch in batches for product in batch]
        except DomainError as exc:
            if exc.status < 500:
                raise
            products = []
            unavailable_sections.append("products")
        mapped = {
            p.id: p.model_dump(mode="json", by_alias=True) | {"seller": seller} for p in products
        }
        items = []
        for post in posts:
            data = post.model_dump(mode="json", by_alias=True)
            data.update(
                seller=seller,
                products=[mapped[i] for i in post.product_ids if i in mapped],
                likesCount=None,
                commentsCount=None,
                isLiked=False,
                unavailableSections=unavailable_sections,
            )
            items.append(data)
        return (
            {
                "items": items,
                "total": result.total,
                "page": result.page,
                "pageSize": result.page_size,
            }
            if page
            else items[0]
        )

    def assert_order(self, store, order):
        if order.seller_id != store.id:
            forbidden("Заказ принадлежит другому магазину")
        return order

    async def orders(self, store, page, page_size, status, q):
        result = await self.state.orders.list(store.id, page, page_size, status, q)
        for order in result.items:
            self.assert_order(store, order)
        return result

    async def order(self, store, order_id):
        return self.assert_order(store, await self.state.orders.get(store.id, order_id))

    async def change_order(self, store, order_id, payload, key):
        order = await self.order(store, order_id)
        allowed = {
            "created": {"cancelled"},
            "paid": {"shipped", "cancelled"},
            "shipped": {"delivered"},
        }
        if payload.status not in allowed.get(order.status, set()):
            invalid("Недопустимый переход статуса", "status")
        result = self.assert_order(
            store,
            await self.state.orders.change(
                store.id,
                order_id,
                payload.model_dump(mode="json", by_alias=True, exclude_none=True),
                key,
            ),
        )
        if result.status != payload.status:
            raise DomainError(
                502, "UPSTREAM_ERROR", "Order Service не подтвердил запрошенный статус"
            )
        return result

    async def dashboard(self, store, start, end):
        now = utcnow()
        start, end = start or now - timedelta(days=30), end or now
        if not start.tzinfo or not end.tzinfo or start >= end or end - start > timedelta(days=366):
            invalid("Период должен содержать UTC/offset, начало < конца, максимум 366 дней")
        values = await asyncio.gather(
            self.state.goods.summary(store.id, self.state.settings.low_stock_threshold),
            self.state.orders.summary(store.id),
            self.state.users.followers(store.id, 1, 1),
            self.state.analytics.views(store.id, start, end),
            return_exceptions=True,
        )
        data = {
            "newOrdersCount": None,
            "awaitingShipmentCount": None,
            "shippedOrdersCount": None,
            "totalOrdersCount": None,
            "totalProductsCount": None,
            "lowStockProducts": None,
            "recentOrders": None,
            "followersCount": None,
            "productViewsCount": None,
            "unavailableSections": [],
            "period": {"from": start, "to": end},
            "quickActions": {
                "createProduct": "/seller/products/new",
                "createPost": "/seller/posts/new",
            },
            "demoData": self.state.settings.external_fake_mode,
        }
        for source, value in zip(("products", "orders", "followers", "views"), values, strict=True):
            if isinstance(value, DomainError):
                data["unavailableSections"].append(source)
            elif isinstance(value, BaseException):
                raise value
            elif source == "products":
                data.update(value)
            elif source == "orders":
                for order in value.recent_orders:
                    self.assert_order(store, order)
                data.update(value.model_dump(mode="json", by_alias=True))
            elif source == "followers":
                data["followersCount"] = value.count
            else:
                data["productViewsCount"] = value
        return data

    async def onboarding(self, store):
        products, posts = await asyncio.gather(
            self.state.goods.list(store.id, page=1, pageSize=1),
            self.state.publications.list(store.id, page=1, pageSize=1, status="published"),
        )
        delivery = await self.stores.delivery(store)
        return {
            "instructions": "Заполните профиль, загрузите изображение, добавьте товар, опубликуйте запись и настройте доставку. Инструкции доступны повторно.",
            "items": [
                {
                    "key": "profile",
                    "completed": bool(store.display_name and store.avatar and store.bio),
                    "url": "/seller/profile",
                },
                {"key": "product", "completed": products.total > 0, "url": "/seller/products/new"},
                {"key": "post", "completed": posts.total > 0, "url": "/seller/posts/new"},
                {
                    "key": "delivery",
                    "completed": delivery.pickup_enabled or delivery.cdek_enabled,
                    "url": "/seller/delivery",
                },
            ],
        }
