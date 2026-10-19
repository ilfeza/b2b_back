from __future__ import annotations

import re
from html import unescape

import bleach
from sqlalchemy import select

from marketplace.publications.models.entities import Post, PostProduct
from marketplace.publications.repositories.posts import PostsRepository
from marketplace.publications.schemas.posts import PostCreate, PostRead
from marketplace.shared.db import paginate, utcnow
from marketplace.shared.errors import DomainError, invalid
from marketplace.shared.messaging.outbox import enqueue

TAGS = ["p", "h1", "h2", "h3", "strong", "em", "s", "ul", "ol", "li", "blockquote", "br"]


def sanitize(body: str) -> str:
    return bleach.clean(
        body, tags=TAGS, attributes={}, protocols=[], strip=True, strip_comments=True
    )


def has_text(body: str) -> bool:
    return bool(unescape(bleach.clean(body, tags=[], strip=True)).strip())


class PostsService:
    def __init__(self, db, policy, goods):
        self.db, self.policy, self.goods = db, policy, goods
        self.repo = PostsRepository(db)

    def serialize(self, post):
        return PostRead(
            id=post.id,
            seller_id=post.seller_id,
            title=post.title,
            body=post.body,
            cover_image=post.cover_image,
            status=post.status,
            product_ids=[x.product_id for x in post.links],
            published_at=post.published_at,
            created_at=post.created_at,
            updated_at=post.updated_at,
        ).model_dump(mode="json", by_alias=True)

    async def validate(self, seller_id, payload, old_ids=()):
        payload = payload.model_copy(
            update={"body": sanitize(payload.body), "title": payload.title.strip()}
        )
        if payload.status == "published" and (not payload.title or not has_text(payload.body)):
            invalid("Для публикации необходимы заголовок и текст", "body")
        # Existing historical links remain valid after a product's soft deletion.
        new_ids = [i for i in payload.product_ids if i not in old_ids]
        if new_ids:
            products = await self.goods.batch(seller_id, new_ids)
            if {p.id for p in products} != set(new_ids):
                invalid("Товар отсутствует, удалён или принадлежит другому продавцу", "productIds")
        if payload.cover_image:
            await self.policy.media(seller_id, payload.cover_image)
        return payload

    async def create(self, seller_id, payload):
        await self.repo.lock(seller_id)
        policy = await self.policy.entitlements(seller_id)
        if policy.post_limit is not None and await self.repo.count(seller_id) >= policy.post_limit:
            raise DomainError(
                403, "QUOTA_EXCEEDED", "Достигнут лимит публикаций", {"limit": policy.post_limit}
            )
        payload = await self.validate(seller_id, payload)
        obj = Post(
            seller_id=seller_id,
            **payload.model_dump(exclude={"product_ids"}, mode="json"),
            published_at=utcnow() if payload.status == "published" else None,
        )
        obj.links = [
            PostProduct(product_id=p, position=n) for n, p in enumerate(payload.product_ids)
        ]
        self.db.add(obj)
        await self.db.flush()
        if obj.status == "published":
            await enqueue(
                self.db,
                "publication.published",
                obj.id,
                {
                    "publicationId": obj.id,
                    "sellerId": seller_id,
                    "status": "published",
                },
            )
        return self.serialize(obj)

    async def update(self, seller_id, post_id, patch):
        await self.repo.lock(seller_id)
        policy = await self.policy.entitlements(seller_id)
        obj = await self.repo.get(seller_id, post_id, True)
        previous_status = obj.status
        merged = self.serialize(obj)
        values = {k: merged[PostCreate.model_fields[k].alias or k] for k in PostCreate.model_fields}
        values.update(patch.model_dump(exclude_unset=True))
        payload = await self.validate(
            seller_id, PostCreate.model_validate(values), [x.product_id for x in obj.links]
        )
        changed = [
            PostCreate.model_fields[k].alias or k
            for k, v in payload.model_dump(mode="json").items()
            if merged[PostCreate.model_fields[k].alias or k] != v
        ]
        if (
            payload.status == "published"
            and policy.post_limit is not None
            and await self.repo.count(seller_id) > policy.post_limit
        ):
            raise DomainError(403, "QUOTA_EXCEEDED", "Превышен лимит публикаций после смены тарифа")
        for key, value in payload.model_dump(exclude={"product_ids"}, mode="json").items():
            setattr(obj, key, value)
        if payload.status == "published" and obj.published_at is None:
            obj.published_at = utcnow()
        # Flush deletions before reinsertion to avoid unique(position) conflicts on reorder.
        obj.links.clear()
        await self.db.flush()
        obj.links.extend(
            PostProduct(product_id=p, position=n) for n, p in enumerate(payload.product_ids)
        )
        await self.db.flush()
        if previous_status != "published" and obj.status == "published":
            await enqueue(
                self.db,
                "publication.published",
                obj.id,
                {
                    "publicationId": obj.id,
                    "sellerId": seller_id,
                    "status": "published",
                },
            )
        elif changed:
            await enqueue(
                self.db,
                "publication.updated",
                obj.id,
                {
                    "publicationId": obj.id,
                    "sellerId": seller_id,
                    "status": obj.status,
                    "changedFields": sorted(changed),
                },
            )
        return self.serialize(obj)

    async def get(self, seller_id, post_id):
        return self.serialize(await self.repo.get(seller_id, post_id))

    async def delete(self, seller_id, post_id):
        await self.repo.lock(seller_id)
        obj = await self.repo.get(seller_id, post_id, True)
        obj.deleted_at = utcnow()
        await self.db.flush()
        await enqueue(
            self.db,
            "publication.deleted",
            obj.id,
            {
                "publicationId": obj.id,
                "sellerId": seller_id,
            },
        )

    async def list(self, seller_id, page, page_size, q, status=None):
        query = self.repo.query(seller_id)
        if q:
            escaped = re.sub(r"([%_\\])", r"\\\1", q)
            query = query.where(Post.title.ilike(f"%{escaped}%", escape="\\"))
        if status:
            query = query.where(Post.status == status)
        return await paginate(self.db, query, page, page_size, self.serialize)

    async def read_published(self, page, page_size, q, seller_id=None):
        query = (
            select(Post)
            .where(Post.status == "published", Post.deleted_at.is_(None))
            .order_by(Post.published_at.desc(), Post.id)
        )
        if seller_id:
            query = query.where(Post.seller_id == seller_id)
        if q:
            escaped = re.sub(r"([%_\\])", r"\\\1", q)
            query = query.where(Post.title.ilike(f"%{escaped}%", escape="\\"))
        return await paginate(self.db, query, page, page_size, self.serialize)

    async def read_one(self, post_id):
        obj = await self.db.scalar(
            select(Post).where(
                Post.id == post_id, Post.status == "published", Post.deleted_at.is_(None)
            )
        )
        if obj is None:
            from marketplace.shared.errors import missing

            missing()
        return self.serialize(obj)

    async def media_references(self, seller_id):
        return list(
            (
                await self.db.scalars(
                    select(Post.cover_image)
                    .where(Post.seller_id == seller_id, Post.cover_image.is_not(None))
                    .distinct()
                )
            ).all()
        )
