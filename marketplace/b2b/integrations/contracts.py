from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from marketplace.shared.schemas import DTO, Identity, OrderStatus, Page


class ExternalDTO(DTO):
    model_config = ConfigDict(extra="allow")


class Follower(DTO):
    id: UUID
    display_name: str
    avatar: str = ""


class Followers(DTO):
    count: int = Field(ge=0)
    items: list[Follower] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class Order(ExternalDTO):
    id: str
    seller_id: UUID
    status: OrderStatus
    created_at: datetime
    items: list[dict]
    total: int = Field(ge=0)
    tracking_number: str | None = None

    @model_validator(mode="before")
    @classmethod
    def seller_from_contract(cls, data):
        if (
            isinstance(data, dict)
            and "sellerId" not in data
            and "seller_id" not in data
            and isinstance(data.get("seller"), dict)
        ):
            return dict(data, sellerId=data["seller"].get("id"))
        return data


class OrderSummary(DTO):
    new_orders_count: int = Field(ge=0)
    awaiting_shipment_count: int = Field(ge=0)
    shipped_orders_count: int = Field(ge=0)
    total_orders_count: int = Field(ge=0)
    recent_orders: list[Order]


class Charge(DTO):
    reference: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,200}$")
    payment_url: str = Field(pattern=r"^https://[^\s]+$")
    status: str


class SubscriptionState(DTO):
    seller_id: UUID
    reference: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,200}$")
    status: str
    tier: str
    valid_until: datetime | None = None
    online_payments: bool = False


class UserService(Protocol):
    async def current(self, token: str) -> Identity: ...
    async def dev_identity(self, user_id: UUID) -> Identity: ...
    async def ensure_seller(self, user_id: UUID, seller_id: UUID) -> Identity: ...
    async def followers(self, seller_id: UUID, page: int, page_size: int) -> Followers: ...


class OrderService(Protocol):
    async def list(
        self, seller_id: UUID, page: int, page_size: int, status: str | None, q: str
    ) -> Page[Order]: ...
    async def get(self, seller_id: UUID, order_id: str) -> Order: ...
    async def change(self, seller_id: UUID, order_id: str, payload: dict, key: str) -> Order: ...
    async def summary(self, seller_id: UUID) -> OrderSummary: ...


class PaymentService(Protocol):
    async def initiate(self, seller_id: UUID, amount: int, key: str) -> Charge: ...
    async def state(self, seller_id: UUID, reference: str) -> SubscriptionState: ...


class Analytics(Protocol):
    async def views(self, seller_id: UUID, start: datetime, end: datetime) -> int: ...


class Storage(Protocol):
    async def upload(self, key: str, body: bytes, content_type: str) -> None: ...
    async def delete(self, key: str) -> None: ...
