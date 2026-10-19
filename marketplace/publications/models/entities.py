from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from marketplace.shared.db import Timestamps, metadata
from marketplace.shared.messaging.models import define_tables


class Base(DeclarativeBase):
    metadata = metadata("publications")


class Author(Base):
    __tablename__ = "seller_authors"
    seller_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)


class Post(Timestamps, Base):
    __tablename__ = "posts"
    seller_id: Mapped[UUID] = mapped_column(
        ForeignKey("publications.seller_authors.seller_id"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), index=True)
    body: Mapped[str] = mapped_column(Text)
    cover_image: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    links: Mapped[list[PostProduct]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PostProduct.position"
    )
    __table_args__ = (CheckConstraint("status IN ('draft','published')", name="status"),)


class PostProduct(Base):
    __tablename__ = "post_products"
    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("publications.posts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("post_id", "position"),
        CheckConstraint("position >= 0", name="positive_position"),
    )


define_tables(Base.metadata)
