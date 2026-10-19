from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from marketplace.goods.schemas.products import ProductRead
from marketplace.publications.schemas.posts import PostRead
from marketplace.shared.schemas import DTO


class SellerRead(DTO):
    id: UUID
    username: str
    display_name: str
    avatar: str
    bio: str
    joined_at: datetime
    followers_count: int | None
    products_count: int | None
    posts_count: int | None
    rating: float | None
    is_following: bool
    unavailable_sections: list[str]


class ProductView(ProductRead):
    seller: SellerRead


class PostView(PostRead):
    seller: SellerRead
    products: list[ProductView]
    likes_count: int | None
    comments_count: int | None
    is_liked: bool
    unavailable_sections: list[str] = Field(default_factory=list)


class StorePublic(DTO):
    id: UUID
    username: str
    display_name: str
    avatar: str
    bio: str
    joined_at: datetime
