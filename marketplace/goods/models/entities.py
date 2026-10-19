from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    literal_column,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from marketplace.shared.db import Timestamps, metadata
from marketplace.shared.messaging.models import define_tables


class Base(DeclarativeBase):
    metadata = metadata("goods")


class Catalog(Base):
    __tablename__ = "seller_catalogs"
    seller_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)


class Product(Timestamps, Base):
    __tablename__ = "products"
    seller_id: Mapped[UUID] = mapped_column(
        ForeignKey("goods.seller_catalogs.seller_id"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    image: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    sale_mode: Mapped[str] = mapped_column(String(30))
    available_deliveries: Mapped[list] = mapped_column(JSON, default=list)
    pickup_points: Mapped[list] = mapped_column(JSON, default=list)
    deal_contacts: Mapped[str | None] = mapped_column(Text)
    in_stock: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        CheckConstraint("price >= 0", name="nonnegative_price"),
        CheckConstraint("in_stock >= 0", name="nonnegative_stock"),
        CheckConstraint("currency = 'RUB'", name="currency"),
        CheckConstraint("sale_mode IN ('online','out_of_stock','deal_only')", name="sale_mode"),
        Index("ix_products_seller_active", "seller_id", "deleted_at"),
    )


search_index = Index(
    "ix_products_title_search",
    func.to_tsvector(literal_column("'simple'::regconfig"), Product.title),
    postgresql_using="gin",
).ddl_if(dialect="postgresql")

Product.__table__.append_constraint(search_index)


define_tables(Base.metadata)
