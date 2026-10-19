from __future__ import annotations

from sqlalchemy import Text, false, func, literal_column, or_, select

from marketplace.goods.models.entities import Product
from marketplace.goods.repositories.catalog import CatalogRepository
from marketplace.goods.schemas.products import ProductCreate, ProductRead
from marketplace.shared.db import paginate, utcnow
from marketplace.shared.errors import DomainError, forbidden, invalid
from marketplace.shared.messaging.outbox import enqueue
from marketplace.shared.schemas import SaleMode


class CatalogService:
    def __init__(self, db, policy):
        self.db, self.policy = db, policy
        self.repo = CatalogRepository(db)

    async def policy_for(self, seller_id):
        return await self.policy.entitlements(seller_id)

    def serialize(self, product, policy):
        data = {key: getattr(product, key) for key in ProductCreate.model_fields}
        mode = product.sale_mode
        if product.deleted_at or (mode == SaleMode.ONLINE and not policy.online_payments):
            mode = SaleMode.OUT_OF_STOCK
        data.update(
            id=product.id,
            seller_id=product.seller_id,
            created_at=product.created_at,
            updated_at=product.updated_at,
            currency=product.currency,
            sale_mode=mode,
            deleted_at=product.deleted_at.isoformat() if product.deleted_at else None,
            purchasable=mode == SaleMode.ONLINE and product.in_stock > 0 and not product.deleted_at,
        )
        return ProductRead.model_validate(data).model_dump(mode="json", by_alias=True)

    async def validate(self, seller_id, payload, policy):
        if policy.tier == "free" and payload.sale_mode == SaleMode.ONLINE:
            forbidden("Free допускает только продажи по договорённости")
        if payload.sale_mode == SaleMode.ONLINE and not policy.online_payments:
            forbidden("Онлайн-продажи не подтверждены платёжным сервисом")
        await self.policy.media(seller_id, payload.image)

    async def create(self, seller_id, payload):
        await self.repo.lock(seller_id)
        policy = await self.policy_for(seller_id)
        if (
            policy.product_limit is not None
            and await self.repo.count(seller_id) >= policy.product_limit
        ):
            raise DomainError(
                403, "QUOTA_EXCEEDED", "Достигнут лимит товаров", {"limit": policy.product_limit}
            )
        await self.validate(seller_id, payload, policy)
        obj = Product(seller_id=seller_id, **payload.model_dump(mode="json"))
        self.db.add(obj)
        await self.db.flush()
        await enqueue(
            self.db,
            "product.created",
            obj.id,
            {
                "productId": obj.id,
                "sellerId": seller_id,
            },
        )
        return self.serialize(obj, policy)

    async def update(self, seller_id, product_id, patch):
        await self.repo.lock(seller_id)
        obj = await self.repo.get(seller_id, product_id, True)
        merged = {key: getattr(obj, key) for key in ProductCreate.model_fields}
        merged.update(patch.model_dump(exclude_unset=True))
        payload = ProductCreate.model_validate(merged)
        changed = [
            ProductCreate.model_fields[k].alias or k
            for k, v in payload.model_dump(mode="json").items()
            if getattr(obj, k) != v
        ]
        policy = await self.policy_for(seller_id)
        await self.validate(seller_id, payload, policy)
        for key, value in payload.model_dump(mode="json").items():
            setattr(obj, key, value)
        await self.db.flush()
        if changed:
            await enqueue(
                self.db,
                "product.updated",
                obj.id,
                {
                    "productId": obj.id,
                    "sellerId": seller_id,
                    "changedFields": sorted(changed),
                },
            )
        return self.serialize(obj, policy)

    async def delete(self, seller_id, product_id):
        await self.repo.lock(seller_id)
        obj = await self.repo.get(seller_id, product_id, True)
        obj.deleted_at = utcnow()
        await self.db.flush()
        await enqueue(
            self.db,
            "product.deleted",
            obj.id,
            {
                "productId": obj.id,
                "sellerId": seller_id,
            },
        )

    async def get(self, seller_id, product_id):
        return self.serialize(
            await self.repo.get(seller_id, product_id), await self.policy_for(seller_id)
        )

    async def list(self, seller_id, page, page_size, q, sale_mode, delivery=None):
        policy = await self.policy_for(seller_id)
        query = self.repo.query(seller_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = Product.title.ilike(f"%{escaped}%", escape="\\")
            if self.db.bind.dialect.name == "postgresql":
                config = literal_column("'simple'::regconfig")
                term = or_(
                    term,
                    func.to_tsvector(config, Product.title).op("@@")(
                        func.websearch_to_tsquery(config, q)
                    ),
                )
            query = query.where(term)
        if sale_mode:
            if not policy.online_payments and sale_mode == SaleMode.ONLINE:
                query = query.where(false())
            elif not policy.online_payments and sale_mode == SaleMode.OUT_OF_STOCK:
                query = query.where(Product.sale_mode.in_([SaleMode.ONLINE, SaleMode.OUT_OF_STOCK]))
            else:
                query = query.where(Product.sale_mode == sale_mode)
        # JSON delivery filtering is performed with a dialect-independent EXISTS-free predicate.
        if delivery:
            query = query.where(Product.available_deliveries.cast(Text).like(f'%"{delivery}"%'))
        return await paginate(self.db, query, page, page_size, lambda p: self.serialize(p, policy))

    async def batch(self, seller_id, payload):
        if len(set(payload.ids)) != len(payload.ids):
            invalid("Повторяющиеся идентификаторы", "ids")
        query = select(Product).where(Product.seller_id == seller_id, Product.id.in_(payload.ids))
        if not payload.include_deleted:
            query = query.where(Product.deleted_at.is_(None))
        found = {p.id: p for p in (await self.db.scalars(query)).all()}
        policy = await self.policy_for(seller_id)
        return [self.serialize(found[i], policy) for i in payload.ids if i in found]

    async def summary(self, seller_id, threshold):
        query = self.repo.query(seller_id).where(Product.in_stock < threshold).limit(100)
        return {
            "totalProductsCount": await self.repo.count(seller_id),
            "lowStockProducts": [
                {"id": str(p.id), "title": p.title, "inStock": p.in_stock}
                for p in (await self.db.scalars(query)).all()
            ],
        }

    async def read_catalog(self, page, page_size, q, seller_id=None, sale_mode=None, delivery=None):
        query = (
            select(Product)
            .where(Product.deleted_at.is_(None))
            .order_by(Product.created_at.desc(), Product.id)
        )
        if seller_id:
            query = query.where(Product.seller_id == seller_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Product.title.ilike(f"%{escaped}%", escape="\\"))
        if delivery:
            query = query.where(Product.available_deliveries.cast(Text).like(f'%"{delivery}"%'))
        # Effective sale mode depends on current entitlements: use the seller-specific
        # API for that filter rather than silently filtering by stale stored values.
        if sale_mode:
            if not seller_id:
                invalid("saleMode требует sellerId для учёта актуального тарифа", "sellerId")
            return await self.list(seller_id, page, page_size, q, sale_mode, delivery)
        result = await paginate(self.db, query, page, page_size, lambda row: row)
        sellers = list(dict.fromkeys(row.seller_id for row in result["items"]))
        policies = {p.seller_id: p for p in await self.policy.batch(sellers)} if sellers else {}
        if set(policies) != set(sellers):
            from marketplace.shared.errors import unavailable

            unavailable("b2b")
        result["items"] = [self.serialize(row, policies[row.seller_id]) for row in result["items"]]
        return result

    async def read_one(self, product_id):
        obj = await self.db.scalar(
            select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
        )
        if obj is None:
            from marketplace.shared.errors import missing

            missing()
        return self.serialize(obj, await self.policy_for(obj.seller_id))

    async def media_references(self, seller_id):
        return list(
            (
                await self.db.scalars(
                    select(Product.image).where(Product.seller_id == seller_id).distinct()
                )
            ).all()
        )

    async def read_batch(self, payload):
        if len(set(payload.ids)) != len(payload.ids):
            invalid("Повторяющиеся идентификаторы", "ids")
        query = select(Product).where(Product.id.in_(payload.ids))
        if not payload.include_deleted:
            query = query.where(Product.deleted_at.is_(None))
        found = {p.id: p for p in (await self.db.scalars(query)).all()}
        sellers = list(dict.fromkeys(p.seller_id for p in found.values()))
        policies = {p.seller_id: p for p in await self.policy.batch(sellers)} if sellers else {}
        if set(policies) != set(sellers):
            from marketplace.shared.errors import unavailable

            unavailable("b2b")
        return [
            self.serialize(found[i], policies[found[i].seller_id])
            for i in payload.ids
            if i in found
        ]
