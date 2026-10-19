from __future__ import annotations

from sqlalchemy import select

from marketplace.b2b.models.entities import Store
from marketplace.shared.errors import missing


class StoresRepository:
    def __init__(self, db):
        self.db = db

    async def by_owner(self, user_id, lock=False):
        query = select(Store).where(Store.owner_user_id == user_id)
        if lock:
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def by_id(self, seller_id, lock=False):
        query = select(Store).where(Store.id == seller_id)
        if lock:
            # Another request may activate an already loaded pending reservation.
            # Refresh the identity-map object AFTER obtaining the row lock.
            query = query.with_for_update().execution_options(populate_existing=True)
        obj = await self.db.scalar(query)
        if obj is None:
            missing()
        return obj
