from __future__ import annotations

from uuid import UUID

from marketplace.b2b.integrations.contracts import (
    Charge,
    Followers,
    Order,
    OrderSummary,
    SubscriptionState,
)
from marketplace.shared.errors import forbidden
from marketplace.shared.http import HTTPClient
from marketplace.shared.readmodels import ViewCount
from marketplace.shared.schemas import Identity, Page


class UserServiceClient(HTTPClient):
    async def current(self, token: str) -> Identity:
        data = await self.request("GET", "/auth/me", headers={"Cookie": f"auth-token={token}"})
        return self.parse(Identity, self.parse(dict, data).get("user"))

    async def dev_identity(self, user_id: UUID) -> Identity:
        forbidden("Dev identity adapter отключён")

    async def ensure_seller(self, user_id: UUID, seller_id: UUID) -> Identity:
        data = await self.request(
            "PUT",
            f"/internal/users/{user_id}/seller-role",
            body={"sellerId": str(seller_id)},
            headers={"Idempotency-Key": str(seller_id)},
        )
        return self.parse(Identity, self.parse(dict, data).get("user"))

    async def followers(self, seller_id, page, page_size):
        return self.parse(
            Followers,
            await self.request(
                "GET",
                f"/internal/sellers/{seller_id}/followers",
                params={"page": page, "pageSize": page_size},
            ),
        )


class OrderServiceClient(HTTPClient):
    async def list(self, seller_id, page, page_size, status, q):
        params = {
            "role": "seller",
            "sellerId": str(seller_id),
            "page": page,
            "pageSize": page_size,
            "q": q,
        }
        if status:
            params["status"] = status
        return self.parse(Page[Order], await self.request("GET", "/orders", params=params))

    async def get(self, seller_id, order_id):
        return self.parse(
            Order,
            await self.request(
                "GET", f"/orders/{order_id}", params={"sellerId": str(seller_id), "role": "seller"}
            ),
        )

    async def change(self, seller_id, order_id, payload, key):
        return self.parse(
            Order,
            await self.request(
                "PATCH",
                f"/orders/{order_id}/status",
                params={"sellerId": str(seller_id)},
                body=payload,
                headers={"Idempotency-Key": key},
            ),
        )

    async def summary(self, seller_id):
        return self.parse(
            OrderSummary, await self.request("GET", f"/internal/sellers/{seller_id}/orders-summary")
        )


class SubscriptionServiceClient(HTTPClient):
    async def initiate(self, seller_id, amount, key):
        return self.parse(
            Charge,
            await self.request(
                "POST",
                "/internal/subscriptions/checkout",
                body={
                    "sellerId": str(seller_id),
                    "tier": "premium",
                    "amount": amount,
                    "currency": "RUB",
                },
                headers={"Idempotency-Key": key},
            ),
        )

    async def state(self, seller_id, reference):
        return self.parse(
            SubscriptionState,
            await self.request(
                "GET", f"/internal/subscriptions/{reference}", params={"sellerId": str(seller_id)}
            ),
        )


class AnalyticsClient(HTTPClient):
    async def views(self, seller_id, start, end):
        data = await self.request(
            "GET",
            f"/internal/sellers/{seller_id}/views",
            params={"from": start.isoformat(), "to": end.isoformat()},
        )
        return self.parse(ViewCount, data).count
