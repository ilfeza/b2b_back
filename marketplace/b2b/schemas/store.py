from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from marketplace.shared.schemas import DTO, OrderStatus, PickupPoint, Text, Tier


class StoreCreate(DTO):
    username: str = Field(pattern=r"^[a-z][a-z0-9_]{2,31}$")
    display_name: Text
    bio: str = Field(default="", max_length=2000)


class StorePatch(DTO):
    display_name: Text | None = None
    bio: str | None = Field(default=None, max_length=2000)
    avatar: str | None = Field(default=None, max_length=2000)


class Delivery(DTO):
    pickup_enabled: bool = False
    pickup_points: list[PickupPoint] = Field(default_factory=list, max_length=30)
    cdek_enabled: bool = False

    @model_validator(mode="after")
    def valid_pickup(self):
        if self.pickup_enabled and not self.pickup_points:
            raise ValueError("pickupPoints обязательны при включенном самовывозе")
        return self


class DeliveryPatch(DTO):
    pickup_enabled: bool | None = None
    pickup_points: list[PickupPoint] | None = Field(default=None, max_length=30)
    cdek_enabled: bool | None = None


class Subscribe(DTO):
    tier: Literal[Tier.PREMIUM]


class StatusChange(DTO):
    status: OrderStatus
    tracking_number: str | None = Field(default=None, max_length=200)
