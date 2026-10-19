from __future__ import annotations

from sqlalchemy import func, select

from marketplace.publications.models.entities import Author, Post
from marketplace.shared.repositories import lock_owner, owned


class PostsRepository:
    def __init__(self, db):
        self.db = db

    async def lock(self, seller_id):
        await lock_owner(self.db, Author, seller_id)

    async def get(self, seller_id, post_id, lock=False):
        return await owned(self.db, Post, post_id, seller_id, lock=lock)

    async def count(self, seller_id):
        return await self.db.scalar(
            select(func.count())
            .select_from(Post)
            .where(Post.seller_id == seller_id, Post.deleted_at.is_(None))
        )

    def query(self, seller_id):
        return (
            select(Post)
            .where(Post.seller_id == seller_id, Post.deleted_at.is_(None))
            .order_by(Post.created_at.desc(), Post.id)
        )
