from __future__ import annotations

from sqlalchemy import func, select

from marketplace.goods.models.entities import Catalog, Product
from marketplace.shared.repositories import lock_owner, owned


class CatalogRepository:
    def __init__(self, db):
        self.db = db

    async def lock(self, seller_id):
        await lock_owner(self.db, Catalog, seller_id)

    async def get(self, seller_id, product_id, lock=False):
        return await owned(self.db, Product, product_id, seller_id, lock=lock)

    async def count(self, seller_id):
        return await self.db.scalar(
            select(func.count())
            .select_from(Product)
            .where(Product.seller_id == seller_id, Product.deleted_at.is_(None))
        )

    def query(self, seller_id):
        return (
            select(Product)
            .where(Product.seller_id == seller_id, Product.deleted_at.is_(None))
            .order_by(Product.created_at.desc(), Product.id)
        )
