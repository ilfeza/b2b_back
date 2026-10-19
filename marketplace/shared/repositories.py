from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from marketplace.shared.errors import missing


async def lock_owner(db, model, seller_id: UUID):
    # Insert-on-conflict creates a durable mutex even for a seller's very first request.
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    await db.execute(insert(model).values(seller_id=seller_id).on_conflict_do_nothing())
    await db.execute(select(model).where(model.seller_id == seller_id).with_for_update())


async def owned(db, model, object_id: UUID, seller_id: UUID, *, lock=False):
    query = select(model).where(
        model.id == object_id, model.seller_id == seller_id, model.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    obj = await db.scalar(query)
    if obj is None:
        missing()
    return obj
