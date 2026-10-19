from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from marketplace.shared.schemas import (
    DTO,
    DeliveryMethod,
    Money,
    PickupPoint,
    SaleMode,
    Stock,
    Text,
    TimestampDTO,
)


class ProductCreate(DTO):
    title: Text
    description: str = Field(default="", max_length=10000)
    image: str = Field(min_length=1, max_length=2000)
    price: Money
    sale_mode: SaleMode = SaleMode.DEAL_ONLY
    available_deliveries: list[DeliveryMethod] = Field(default_factory=list, max_length=2)
    pickup_points: list[PickupPoint] = Field(default_factory=list, max_length=30)
    deal_contacts: str | None = Field(default=None, max_length=2000)
    in_stock: Stock = 0

    @model_validator(mode="after")
    def consistency(self):
        if DeliveryMethod.PICKUP in self.available_deliveries and not self.pickup_points:
            raise ValueError("pickupPoints обязательны для pickup")
        if self.sale_mode == SaleMode.DEAL_ONLY and not (self.deal_contacts or "").strip():
            raise ValueError("dealContacts обязательны для deal_only")
        if len(set(self.available_deliveries)) != len(self.available_deliveries):
            raise ValueError("Способы доставки не должны повторяться")
        if self.sale_mode == SaleMode.ONLINE and (
            not self.in_stock or not self.available_deliveries
        ):
            raise ValueError("online требует положительный остаток и способ доставки")
        return self


class ProductPatch(DTO):
    title: Text | None = None
    description: str | None = Field(default=None, max_length=10000)
    image: str | None = Field(default=None, min_length=1, max_length=2000)
    price: Money | None = None
    sale_mode: SaleMode | None = None
    available_deliveries: list[DeliveryMethod] | None = Field(default=None, max_length=2)
    pickup_points: list[PickupPoint] | None = Field(default=None, max_length=30)
    deal_contacts: str | None = Field(default=None, max_length=2000)
    in_stock: Stock | None = None


class ProductRead(TimestampDTO, ProductCreate):
    seller_id: UUID
    currency: Literal["RUB"] = "RUB"
    deleted_at: str | None = None
    purchasable: bool = False


class Batch(DTO):
    ids: Annotated[list[UUID], Field(max_length=100)]
    include_deleted: bool = False
