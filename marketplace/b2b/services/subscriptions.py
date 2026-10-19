from __future__ import annotations

from sqlalchemy import select

from marketplace.b2b.models.entities import Subscription
from marketplace.b2b.services.stores import aware
from marketplace.shared.db import utcnow
from marketplace.shared.errors import DomainError, invalid


class SubscriptionService:
    def __init__(self, db, state):
        self.db, self.state = db, state

    def plans(self):
        return [
            {
                "tier": "free",
                "name": "Free",
                "price": 0,
                "features": [
                    {
                        "label": f"Товаров до {self.state.settings.free_product_limit}",
                        "included": True,
                    },
                    {
                        "label": f"Публикаций до {self.state.settings.free_post_limit}",
                        "included": True,
                    },
                    {"label": "Продажи по договорённости", "included": True},
                    {"label": "Онлайн-оплата", "included": False},
                ],
            },
            {
                "tier": "premium",
                "name": "Premium",
                "price": self.state.settings.premium_price,
                "features": [
                    {"label": "Товары и публикации без лимита", "included": True},
                    {
                        "label": "Онлайн-продажи после подтверждения платежных прав",
                        "included": True,
                    },
                    {"label": "Расширенная аналитика (не реализована в MVP)", "included": False},
                    {"label": "Приоритет в поиске (не реализован в MVP)", "included": False},
                ],
            },
        ]

    async def initiate(self, store, key):
        sub = await self.db.scalar(
            select(Subscription).where(Subscription.seller_id == store.id).with_for_update()
        )
        if (
            sub.provider_reference
            and sub.status in {"pending", "confirmed"}
            and (not sub.valid_until or aware(sub.valid_until) > utcnow())
        ):
            if sub.checkout_key != key:
                raise DomainError(
                    409, "CONFLICT", "Предыдущая подписка активна или ожидает подтверждения"
                )
            return {
                "reference": sub.provider_reference,
                "paymentUrl": sub.payment_url,
                "status": sub.status,
            }
        charge = await self.state.payments.initiate(
            store.id, self.state.settings.premium_price, key
        )
        sub.checkout_key, sub.payment_url = key, charge.payment_url
        sub.provider_reference = charge.reference
        sub.status = "pending"
        sub.online_payments = False
        await self.db.flush()
        return {
            "reference": charge.reference,
            "paymentUrl": charge.payment_url,
            "status": "pending" if sub.status != "confirmed" else "confirmed",
        }

    async def sync(self, store):
        sub = await self.db.scalar(
            select(Subscription).where(Subscription.seller_id == store.id).with_for_update()
        )
        if not sub.provider_reference:
            invalid("Сначала инициируйте подписку")
        confirmed = await self.state.payments.state(store.id, sub.provider_reference)
        if confirmed.seller_id != store.id or confirmed.reference != sub.provider_reference:
            raise DomainError(502, "UPSTREAM_ERROR", "Несогласованный ответ Subscription Service")
        if confirmed.status == "confirmed":
            if (
                confirmed.tier != "premium"
                or not aware(confirmed.valid_until)
                or aware(confirmed.valid_until) <= utcnow()
            ):
                raise DomainError(502, "UPSTREAM_ERROR", "Некорректное подтверждение подписки")
            sub.status, sub.valid_until, sub.online_payments = (
                "confirmed",
                confirmed.valid_until,
                confirmed.online_payments,
            )
            store.plan_tier = "premium"
        elif confirmed.status in {"rejected", "expired", "cancelled"}:
            sub.status, sub.online_payments, store.plan_tier = confirmed.status, False, "free"
        elif confirmed.status != "pending":
            raise DomainError(502, "UPSTREAM_ERROR", "Неизвестное состояние подписки")
        await self.db.flush()
        return {"status": sub.status, "tier": store.plan_tier, "validUntil": sub.valid_until}
