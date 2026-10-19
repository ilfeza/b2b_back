from __future__ import annotations

from uuid import UUID

from marketplace.goods.schemas.products import Batch, ProductCreate, ProductPatch, ProductRead
from marketplace.publications.schemas.posts import PostCreate, PostPatch, PostRead
from marketplace.shared.http import HTTPClient
from marketplace.shared.readmodels import GoodsSummary
from marketplace.shared.schemas import Entitlements, Page


class PolicyClient(HTTPClient):
    async def entitlements(self, seller_id: UUID) -> Entitlements:
        return self.parse(
            Entitlements, await self.request("GET", f"/internal/sellers/{seller_id}/entitlements")
        )

    async def batch(self, ids: list[UUID]) -> list[Entitlements]:
        return self.parse(
            list[Entitlements],
            await self.request(
                "POST", "/internal/sellers/entitlements/batch", body={"ids": [str(i) for i in ids]}
            ),
        )

    async def media(self, seller_id: UUID, url: str) -> None:
        await self.request(
            "POST", f"/internal/sellers/{seller_id}/media/validate", body={"url": url}
        )


class GoodsClient(HTTPClient):
    async def list(self, seller_id: UUID, **params) -> Page[ProductRead]:
        return self.parse(
            Page[ProductRead],
            await self.request("GET", f"/internal/sellers/{seller_id}/products", params=params),
        )

    async def get(self, seller_id: UUID, product_id: UUID) -> ProductRead:
        return self.parse(
            ProductRead,
            await self.request("GET", f"/internal/sellers/{seller_id}/products/{product_id}"),
        )

    async def create(self, seller_id: UUID, payload: ProductCreate) -> ProductRead:
        return self.parse(
            ProductRead,
            await self.request(
                "POST",
                f"/internal/sellers/{seller_id}/products",
                body=payload.model_dump(mode="json", by_alias=True),
            ),
        )

    async def update(self, seller_id: UUID, product_id: UUID, payload: ProductPatch) -> ProductRead:
        return self.parse(
            ProductRead,
            await self.request(
                "PATCH",
                f"/internal/sellers/{seller_id}/products/{product_id}",
                body=payload.model_dump(mode="json", by_alias=True, exclude_unset=True),
            ),
        )

    async def delete(self, seller_id: UUID, product_id: UUID) -> None:
        await self.request("DELETE", f"/internal/sellers/{seller_id}/products/{product_id}")

    async def batch(
        self, seller_id: UUID, ids: list[UUID], include_deleted=False
    ) -> list[ProductRead]:
        data = await self.request(
            "POST",
            f"/internal/sellers/{seller_id}/products/batch",
            body=Batch(ids=ids, include_deleted=include_deleted).model_dump(
                mode="json", by_alias=True
            ),
        )
        return self.parse(list[ProductRead], data)

    async def summary(self, seller_id: UUID, threshold: int) -> dict:
        result = self.parse(
            GoodsSummary,
            await self.request(
                "GET",
                f"/internal/sellers/{seller_id}/products-summary",
                params={"threshold": threshold},
            ),
        )
        return result.model_dump(mode="json", by_alias=True)


class PublicationsClient(HTTPClient):
    async def list(self, seller_id: UUID, **params) -> Page[PostRead]:
        return self.parse(
            Page[PostRead],
            await self.request("GET", f"/internal/sellers/{seller_id}/posts", params=params),
        )

    async def get(self, seller_id: UUID, post_id: UUID) -> PostRead:
        return self.parse(
            PostRead, await self.request("GET", f"/internal/sellers/{seller_id}/posts/{post_id}")
        )

    async def create(self, seller_id: UUID, payload: PostCreate) -> PostRead:
        return self.parse(
            PostRead,
            await self.request(
                "POST",
                f"/internal/sellers/{seller_id}/posts",
                body=payload.model_dump(mode="json", by_alias=True),
            ),
        )

    async def update(self, seller_id: UUID, post_id: UUID, payload: PostPatch) -> PostRead:
        return self.parse(
            PostRead,
            await self.request(
                "PATCH",
                f"/internal/sellers/{seller_id}/posts/{post_id}",
                body=payload.model_dump(mode="json", by_alias=True, exclude_unset=True),
            ),
        )

    async def delete(self, seller_id: UUID, post_id: UUID) -> None:
        await self.request("DELETE", f"/internal/sellers/{seller_id}/posts/{post_id}")

    async def publish(self, seller_id: UUID, post_id: UUID) -> PostRead:
        return self.parse(
            PostRead,
            await self.request("POST", f"/internal/sellers/{seller_id}/posts/{post_id}/publish"),
        )
