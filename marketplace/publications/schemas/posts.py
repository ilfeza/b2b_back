from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from marketplace.shared.schemas import DTO, PostStatus, TimestampDTO


class PostCreate(DTO):
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=100000)
    cover_image: str | None = Field(default=None, max_length=2000)
    status: PostStatus = PostStatus.DRAFT
    product_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_links(self):
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("productIds не должны повторяться")
        return self


class PostPatch(DTO):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=100000)
    cover_image: str | None = Field(default=None, max_length=2000)
    status: PostStatus | None = None
    product_ids: list[UUID] | None = Field(default=None, max_length=100)


class PostRead(TimestampDTO, PostCreate):
    seller_id: UUID
    published_at: datetime | None = None
