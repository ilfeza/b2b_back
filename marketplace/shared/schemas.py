from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel


class DTO(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", from_attributes=True
    )


T = TypeVar("T")
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
Stock = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]


class APIError(DTO):
    code: str
    message: str
    details: dict | None = None
    request_id: str | None = None


class Envelope[T](DTO):
    data: T | None = None
    error: APIError | None = None


class Page[T](DTO):
    items: list[T]
    total: int
    page: int
    page_size: int


class SaleMode(StrEnum):
    ONLINE = "online"
    OUT_OF_STOCK = "out_of_stock"
    DEAL_ONLY = "deal_only"


class DeliveryMethod(StrEnum):
    PICKUP = "pickup"
    CDEK = "cdek"


class PostStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class Tier(StrEnum):
    FREE = "free"
    PREMIUM = "premium"


class OrderStatus(StrEnum):
    CREATED = "created"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PickupPoint(DTO):
    city: Text
    address: Text


class Identity(DTO):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    roles: set[str]
    display_name: str = ""
    username: str = ""
    avatar: str = ""
    email: str = ""
    bio: str = ""
    created_at: datetime | None = None


class Entitlements(DTO):
    seller_id: UUID
    tier: Tier
    product_limit: int | None
    post_limit: int | None
    online_payments: bool = False


class TimestampDTO(DTO):
    id: UUID
    created_at: datetime
    updated_at: datetime
