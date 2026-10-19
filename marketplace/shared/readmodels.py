from uuid import UUID

from pydantic import Field

from marketplace.shared.schemas import DTO, Stock, Text


class LowStock(DTO):
    id: UUID
    title: Text
    in_stock: Stock


class GoodsSummary(DTO):
    total_products_count: int = Field(ge=0)
    low_stock_products: list[LowStock]


class SellerBatch(DTO):
    ids: list[UUID] = Field(max_length=100)


class ViewCount(DTO):
    count: int = Field(ge=0, strict=True)
