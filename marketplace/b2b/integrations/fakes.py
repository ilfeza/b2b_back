"""Explicit dev/test adapters. Never selected in production."""

from __future__ import annotations

from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from marketplace.b2b.integrations.contracts import (
    Charge,
    Followers,
    OrderSummary,
    SubscriptionState,
)
from marketplace.shared.db import utcnow
from marketplace.shared.errors import missing
from marketplace.shared.schemas import Identity, Page

DEMO_USER = UUID("11111111-1111-4111-8111-111111111111")
BUYER_USER = UUID("22222222-2222-4222-8222-222222222222")


class FakeUsers:
    def __init__(self):
        self.roles = {DEMO_USER: {"buyer", "seller"}, BUYER_USER: {"buyer"}}

    async def dev_identity(self, user_id):
        # Only two configured dev identities, no arbitrary role headers.
        if user_id not in self.roles:
            from marketplace.shared.errors import DomainError

            raise DomainError(401, "UNAUTHORIZED", "Неизвестная dev identity")
        return Identity(id=user_id, roles=self.roles[user_id], display_name="Демо пользователь")

    async def current(self, token):
        return await self.dev_identity(DEMO_USER)

    async def ensure_seller(self, user_id, seller_id):
        self.roles.setdefault(user_id, {"buyer"}).add("seller")
        return await self.dev_identity(user_id)

    async def followers(self, seller_id, page, page_size):
        return Followers(count=0, items=[], page=page, page_size=page_size)


class FakeOrders:
    async def list(self, seller_id, page, page_size, status, q):
        return Page(items=[], total=0, page=page, page_size=page_size)

    async def get(self, seller_id, order_id):
        missing()

    async def change(self, seller_id, order_id, payload, key):
        missing()

    async def summary(self, seller_id):
        return OrderSummary(
            new_orders_count=0,
            awaiting_shipment_count=0,
            shipped_orders_count=0,
            total_orders_count=0,
            recent_orders=[],
        )


class FakePayments:
    def __init__(self):
        self.confirmed = False
        self.rejected = False

    async def initiate(self, seller_id, amount, key):
        reference = str(uuid5(NAMESPACE_URL, f"{seller_id}:{key}"))
        return Charge(
            reference=reference,
            payment_url=f"https://example.invalid/dev-payment/{reference}",
            status="pending",
        )

    async def state(self, seller_id, reference):
        return SubscriptionState(
            seller_id=seller_id,
            reference=reference,
            status="rejected" if self.rejected else "confirmed" if self.confirmed else "pending",
            tier="premium",
            valid_until=utcnow() + timedelta(days=30) if self.confirmed else None,
            online_payments=self.confirmed,
        )


class FakeAnalytics:
    async def views(self, seller_id, start, end):
        return 0


class FakeStorage:
    def __init__(self):
        self.objects = {}

    async def upload(self, key, body, content_type):
        self.objects[key] = (body, content_type)

    async def delete(self, key):
        self.objects.pop(key, None)
