from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marketplace.b2b.models.entities import DeliverySettings, Media, Store, Subscription
from marketplace.b2b.repositories.stores import StoresRepository
from marketplace.b2b.schemas.store import Delivery, StorePatch
from marketplace.shared.db import utcnow
from marketplace.shared.errors import DomainError, forbidden, invalid
from marketplace.shared.messaging.outbox import enqueue
from marketplace.shared.schemas import Entitlements, Tier


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class StoreService:
    def __init__(self, db, state):
        self.db, self.state = db, state
        self.repo = StoresRepository(db)

    async def current(self, user, lock=False):
        if "seller" not in user.roles:
            forbidden("Требуется роль seller")
        store = await self.repo.by_owner(user.id, lock)
        if store is None or store.onboarding_status != "active":
            forbidden("Завершите регистрацию магазина")
        return store

    async def onboard(self, user, payload):
        store = await self.repo.by_owner(user.id)
        if store is None:
            store = Store(
                owner_user_id=user.id,
                **payload.model_dump(),
                onboarding_status="pending",
                plan_tier="free",
            )
            self.db.add(store)
            try:
                await self.db.flush()
                self.db.add(DeliverySettings(seller_id=store.id))
                self.db.add(Subscription(seller_id=store.id, status="free"))
                # Persist a recoverable reservation before contacting the role owner.
                await self.db.commit()
            except IntegrityError:
                await self.db.rollback()
                store = await self.repo.by_owner(user.id)
                if store is None:
                    raise DomainError(409, "CONFLICT", "Username занят") from None
        store = await self.repo.by_id(store.id, lock=True)
        was_active = store.onboarding_status == "active"
        confirmed = await self.state.users.ensure_seller(user.id, store.id)
        if confirmed.id != user.id or "seller" not in confirmed.roles:
            raise DomainError(502, "UPSTREAM_ERROR", "User Service не подтвердил роль seller")
        store.onboarding_status = "active"
        await self.db.flush()
        if not was_active:
            await enqueue(
                self.db,
                "seller.created",
                store.id,
                {
                    "sellerId": store.id,
                    "ownerUserId": store.owner_user_id,
                    "username": store.username,
                },
            )
        return confirmed, store

    async def entitlement(self, seller_id):
        store = await self.repo.by_id(seller_id)
        if store.onboarding_status != "active":
            forbidden("Магазин не активирован")
        sub = await self.db.scalar(select(Subscription).where(Subscription.seller_id == seller_id))
        return self.entitlement_values(store, sub)

    def entitlement_values(self, store, sub):
        premium = bool(
            store.plan_tier == "premium"
            and sub
            and sub.status == "confirmed"
            and aware(sub.valid_until)
            and aware(sub.valid_until) > utcnow()
        )
        return Entitlements(
            seller_id=store.id,
            tier=Tier.PREMIUM if premium else Tier.FREE,
            product_limit=None if premium else self.state.settings.free_product_limit,
            post_limit=None if premium else self.state.settings.free_post_limit,
            online_payments=bool(premium and sub.online_payments),
        )

    async def entitlement_batch(self, ids):
        rows = (
            await self.db.execute(
                select(Store, Subscription)
                .join(Subscription, Subscription.seller_id == Store.id)
                .where(Store.id.in_(ids), Store.onboarding_status == "active")
            )
        ).all()
        mapped = {store.id: self.entitlement_values(store, sub) for store, sub in rows}
        return [mapped[i] for i in ids if i in mapped]

    async def validate_media(self, seller_id, url):
        media = await self.db.scalar(
            select(Media).where(Media.owner_seller_id == seller_id, Media.url == url)
        )
        if media is None:
            invalid("Используйте URL изображения, загруженного этим магазином", "image")
        return media

    async def update(self, store, payload: StorePatch):
        values = payload.model_dump(exclude_unset=True)
        for key, value in values.items():
            if value is None:
                invalid("Поле не может быть null", key)
        if values.get("avatar"):
            await self.validate_media(store.id, values["avatar"])
        for key, value in values.items():
            setattr(store, key, value)
        await self.db.flush()
        return store

    async def delivery(self, store):
        obj = await self.db.get(DeliverySettings, store.id)
        return Delivery.model_validate(obj)

    async def update_delivery(self, store, patch):
        obj = await self.db.get(DeliverySettings, store.id)
        values = (await self.delivery(store)).model_dump()
        values.update(patch.model_dump(exclude_unset=True))
        checked = Delivery.model_validate(values)
        for key, value in checked.model_dump(mode="json").items():
            setattr(obj, key, value)
        await self.db.flush()
        return checked

    async def public_batch(self, ids):
        from marketplace.b2b.schemas.responses import StorePublic

        stores = (
            await self.db.scalars(
                select(Store).where(Store.id.in_(ids), Store.onboarding_status == "active")
            )
        ).all()
        mapped = {
            s.id: StorePublic(
                id=s.id,
                username=s.username,
                display_name=s.display_name,
                avatar=s.avatar,
                bio=s.bio,
                joined_at=s.created_at,
            )
            for s in stores
        }
        return [mapped[i] for i in ids if i in mapped]
