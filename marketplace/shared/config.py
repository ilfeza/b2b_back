from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: Literal["development", "test", "production"] = "production"
    service: Literal["b2b", "goods", "publications"] = "b2b"
    database_url: str = "postgresql+asyncpg://b2b:b2b@localhost:5432/marketplace"
    api_prefix: str = "/api"
    public_storefront_prefix: str = "/shops"
    auth_dev_mode: bool = False
    external_fake_mode: bool = False
    service_key: str = ""
    cors_origins: list[str] = ["http://localhost:3000"]
    jwt_public_key: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = "handmade"
    user_service_url: str = "http://users:8000/api"
    order_service_url: str = "http://orders:8000/api"
    payment_service_url: str = "http://orders:8000/api"
    analytics_service_url: str = "http://analytics:8000/api"
    goods_service_url: str = "http://goods:8000/api"
    publications_service_url: str = "http://publications:8000/api"
    b2b_service_url: str = "http://b2b:8000/api"
    upstream_timeout_seconds: float = 5.0
    premium_price: int = 990
    free_product_limit: int = 10
    free_post_limit: int = 5
    low_stock_threshold: int = 3
    max_upload_bytes: int = 5 * 1024 * 1024
    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_b2b_group_id: str = "handmade.b2b.v1"
    kafka_goods_group_id: str = "handmade.goods.v1"
    kafka_publications_group_id: str = "handmade.publications.v1"
    kafka_sellers_topic: str = "handmade.sellers.v1"
    kafka_products_topic: str = "handmade.products.v1"
    kafka_publications_topic: str = "handmade.publications.v1"
    kafka_orders_topic: str = "handmade.orders.v1"
    kafka_b2b_dlq_topic: str = "handmade.dlq.b2b.v1"
    kafka_goods_dlq_topic: str = "handmade.dlq.goods.v1"
    kafka_publications_dlq_topic: str = "handmade.dlq.publications.v1"
    kafka_orders_enabled: bool = False
    kafka_poll_interval_seconds: float = 0.5
    kafka_retry_base_seconds: float = 1.0
    kafka_retry_max_seconds: float = 30.0
    kafka_delivery_timeout_seconds: float = 5.0
    kafka_shutdown_timeout_seconds: float = 10.0
    kafka_handler_max_attempts: int = 3
    kafka_max_event_bytes: int = 262144
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "handmade-media"
    s3_public_url: str = "http://localhost:9000/handmade-media"

    @property
    def kafka_group_id(self) -> str:
        return getattr(self, f"kafka_{self.service}_group_id")

    @property
    def kafka_dlq_topic(self) -> str:
        return getattr(self, f"kafka_{self.service}_dlq_topic")

    @property
    def kafka_subscriptions(self) -> dict[str, set[str]]:
        if self.service != "b2b":
            return {self.kafka_sellers_topic: {"seller.created"}}
        topics = {
            self.kafka_products_topic: {"product.created", "product.updated", "product.deleted"},
            self.kafka_publications_topic: {
                "publication.published",
                "publication.updated",
                "publication.deleted",
            },
        }
        if self.kafka_orders_enabled:
            topics[self.kafka_orders_topic] = {"order.status_changed"}
        return topics

    def kafka_topic_for(self, event_type: str) -> str:
        return {
            "seller": self.kafka_sellers_topic,
            "product": self.kafka_products_topic,
            "publication": self.kafka_publications_topic,
            "message": self.kafka_dlq_topic,
        }[event_type.split(".")[0]]

    @model_validator(mode="after")
    def secure_configuration(self):
        import re

        topics = [getattr(self, key) for key in type(self).model_fields if key.endswith("_topic")]
        if len(set(topics)) != len(topics) or any(
            not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9._-]{0,248}", topic) for topic in topics
        ):
            raise ValueError("Kafka topics must be distinct valid topic names")
        groups = [
            self.kafka_b2b_group_id,
            self.kafka_goods_group_id,
            self.kafka_publications_group_id,
        ]
        if len(set(groups)) != 3 or any(not g or len(g) > 160 for g in groups):
            raise ValueError("Kafka consumer groups must be distinct, nonempty, at most 160 chars")
        if (
            min(
                self.kafka_poll_interval_seconds,
                self.kafka_retry_base_seconds,
                self.kafka_delivery_timeout_seconds,
                self.kafka_shutdown_timeout_seconds,
            )
            <= 0
        ):
            raise ValueError("Kafka timeouts must be positive")
        if self.kafka_retry_max_seconds < self.kafka_retry_base_seconds:
            raise ValueError("Kafka max retry must not be below base retry")
        if self.kafka_handler_max_attempts < 1 or not 1024 <= self.kafka_max_event_bytes <= 524288:
            raise ValueError("Invalid Kafka retry or event size limits")
        if self.environment == "production":
            if self.auth_dev_mode or self.external_fake_mode:
                raise ValueError("Development adapters are forbidden in production")
            if len(self.service_key) < 32:
                raise ValueError("SERVICE_KEY must have at least 32 characters")
            if self.service == "b2b" and (not self.jwt_public_key or not self.jwt_issuer):
                raise ValueError("JWT_PUBLIC_KEY and JWT_ISSUER are required")
        if (
            not self.public_storefront_prefix.startswith("/")
            or "?" in self.public_storefront_prefix
            or "#" in self.public_storefront_prefix
        ):
            raise ValueError("PUBLIC_STOREFRONT_PREFIX must be an absolute URL path")
        if not self.api_prefix.startswith("/") or self.api_prefix.endswith("/"):
            raise ValueError("API_PREFIX must start with / and have no trailing slash")
        if min(self.free_product_limit, self.free_post_limit, self.premium_price) < 1:
            raise ValueError("Limits and price must be positive")
        return self
