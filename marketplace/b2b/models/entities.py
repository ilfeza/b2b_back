from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from marketplace.shared.db import Timestamps, metadata
from marketplace.shared.messaging.models import define_tables


class Base(DeclarativeBase):
    metadata = metadata("b2b")


class Store(Timestamps, Base):
    __tablename__ = "seller_stores"
    owner_user_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200))
    bio: Mapped[str] = mapped_column(Text, default="")
    avatar: Mapped[str] = mapped_column(Text, default="")
    plan_tier: Mapped[str] = mapped_column(String(20), default="free")
    onboarding_status: Mapped[str] = mapped_column(String(20), default="pending")
    __table_args__ = (
        CheckConstraint("plan_tier IN ('free','premium')", name="plan_tier"),
        CheckConstraint("onboarding_status IN ('pending','active')", name="onboarding_status"),
    )


class DeliverySettings(Base):
    __tablename__ = "seller_delivery_settings"
    seller_id: Mapped[UUID] = mapped_column(ForeignKey("b2b.seller_stores.id"), primary_key=True)
    pickup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cdek_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pickup_points: Mapped[list] = mapped_column(JSON, default=list)


class Media(Timestamps, Base):
    __tablename__ = "media_files"
    owner_seller_id: Mapped[UUID] = mapped_column(ForeignKey("b2b.seller_stores.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("size > 0", name="positive_size"),)


class Subscription(Timestamps, Base):
    __tablename__ = "seller_subscriptions"
    seller_id: Mapped[UUID] = mapped_column(ForeignKey("b2b.seller_stores.id"), unique=True)
    checkout_key: Mapped[str | None] = mapped_column(String(100))
    payment_url: Mapped[str | None] = mapped_column(Text)
    provider_reference: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="free")
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    online_payments: Mapped[bool] = mapped_column(Boolean, default=False)


define_tables(Base.metadata)
