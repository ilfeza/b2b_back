from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator, model_validator

from marketplace.shared.schemas import DTO, OrderStatus


class SellerPayload(DTO):
    seller_id: UUID
    owner_user_id: UUID
    username: str = Field(pattern=r"^[a-z][a-z0-9_]{2,31}$")


class ProductPayload(DTO):
    product_id: UUID
    seller_id: UUID


class ProductUpdatedPayload(ProductPayload):
    changed_fields: list[
        Literal[
            "title",
            "description",
            "image",
            "price",
            "saleMode",
            "availableDeliveries",
            "pickupPoints",
            "dealContacts",
            "inStock",
        ]
    ] = Field(min_length=1, max_length=9)


class PublicationPayload(DTO):
    publication_id: UUID
    seller_id: UUID


class PublicationPublishedPayload(PublicationPayload):
    status: Literal["published"] = "published"


class PublicationUpdatedPayload(PublicationPayload):
    status: Literal["draft", "published"]
    changed_fields: list[Literal["title", "body", "coverImage", "status", "productIds"]] = Field(
        min_length=1, max_length=5
    )


class OrderPayload(DTO):
    order_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    seller_id: UUID
    previous_status: OrderStatus
    status: OrderStatus


class RejectedPayload(DTO):
    consumer_group: str
    original_topic: str
    original_partition: int = Field(ge=0)
    original_offset: int = Field(ge=0)
    reason_code: str
    attempts: int = Field(ge=1)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_size: int = Field(ge=0)
    original_value_base64: str
    truncated: bool


class Event(DTO):
    event_id: UUID
    version: Literal[1]
    occurred_at: AwareDatetime
    aggregate_id: str = Field(min_length=1, max_length=200)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @field_validator("occurred_at", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("occurredAt must be an ISO timestamp")
        return value

    @field_validator("occurred_at")
    @classmethod
    def utc(cls, value):
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def aggregate_matches_payload(self):
        for name in ("product_id", "publication_id", "order_id"):
            if hasattr(self.payload, name):
                expected = getattr(self.payload, name)
                break
        else:
            expected = getattr(self.payload, "seller_id", self.aggregate_id)
        if self.aggregate_id != str(expected):
            raise ValueError("aggregateId does not match payload identity")
        return self


class SellerCreated(Event):
    event_type: Literal["seller.created"]
    payload: SellerPayload


class ProductCreated(Event):
    event_type: Literal["product.created"]
    payload: ProductPayload


class ProductUpdated(Event):
    event_type: Literal["product.updated"]
    payload: ProductUpdatedPayload


class ProductDeleted(Event):
    event_type: Literal["product.deleted"]
    payload: ProductPayload


class PublicationPublished(Event):
    event_type: Literal["publication.published"]
    payload: PublicationPublishedPayload


class PublicationUpdated(Event):
    event_type: Literal["publication.updated"]
    payload: PublicationUpdatedPayload


class PublicationDeleted(Event):
    event_type: Literal["publication.deleted"]
    payload: PublicationPayload


class OrderStatusChanged(Event):
    event_type: Literal["order.status_changed"]
    payload: OrderPayload


class MessageRejected(Event):
    event_type: Literal["message.rejected"]
    payload: RejectedPayload


EVENTS = TypeAdapter(
    Annotated[
        SellerCreated
        | ProductCreated
        | ProductUpdated
        | ProductDeleted
        | PublicationPublished
        | PublicationUpdated
        | PublicationDeleted
        | OrderStatusChanged
        | MessageRejected,
        Field(discriminator="event_type"),
    ]
)
