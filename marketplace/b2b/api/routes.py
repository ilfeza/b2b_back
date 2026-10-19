from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, UploadFile
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from marketplace.b2b.models.entities import Store
from marketplace.b2b.schemas.responses import PostView, ProductView, StorePublic
from marketplace.b2b.schemas.store import (
    DeliveryPatch,
    StatusChange,
    StoreCreate,
    StorePatch,
    Subscribe,
)
from marketplace.b2b.services.facade import SellerFacade
from marketplace.b2b.services.media import MediaService
from marketplace.b2b.services.stores import StoreService
from marketplace.b2b.services.subscriptions import SubscriptionService
from marketplace.goods.schemas.products import ProductCreate, ProductPatch
from marketplace.publications.schemas.posts import PostCreate, PostPatch
from marketplace.shared.db import session
from marketplace.shared.readmodels import SellerBatch
from marketplace.shared.schemas import (
    DTO,
    Entitlements,
    Envelope,
    Identity,
    OrderStatus,
    Page,
    PostStatus,
    SaleMode,
)
from marketplace.shared.security import identity, internal

router = APIRouter(tags=["Seller API"])
DB = Annotated[AsyncSession, Depends(session, scope="function")]
User = Annotated[Identity, Depends(identity)]
Key = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
]


@dataclass
class Context:
    store: Store
    svc: StoreService
    facade: SellerFacade
    request: Request


async def context(request: Request, db: DB, user: User):
    svc = StoreService(db, request.app.state)
    return Context(await svc.current(user), svc, SellerFacade(db, request.app.state), request)


Ctx = Annotated[Context, Depends(context)]


@router.post("/users/me/become-seller", response_model=Envelope[dict])
async def onboard(request: Request, db: DB, user: User, payload: StoreCreate):
    svc = StoreService(db, request.app.state)
    confirmed, store = await svc.onboard(user, payload)
    return {
        "data": {
            "user": confirmed,
            "seller": await SellerFacade(db, request.app.state).profile(store),
        }
    }


@router.get("/seller/profile", response_model=Envelope[dict])
async def profile(ctx: Ctx):
    return {"data": await ctx.facade.profile(ctx.store)}


@router.patch("/seller/profile", response_model=Envelope[dict])
async def update_profile(ctx: Ctx, payload: StorePatch):
    store = await ctx.svc.repo.by_id(ctx.store.id, lock=True)
    await ctx.svc.update(store, payload)
    return {"data": await ctx.facade.profile(store)}


@router.get("/seller/delivery", response_model=Envelope[dict])
async def delivery(ctx: Ctx):
    return {"data": (await ctx.svc.delivery(ctx.store)).model_dump(by_alias=True)}


@router.patch("/seller/delivery", response_model=Envelope[dict])
async def update_delivery(ctx: Ctx, payload: DeliveryPatch):
    store = await ctx.svc.repo.by_id(ctx.store.id, lock=True)
    return {"data": (await ctx.svc.update_delivery(store, payload)).model_dump(by_alias=True)}


@router.get("/seller/onboarding", response_model=Envelope[dict])
async def checklist(ctx: Ctx):
    return {"data": await ctx.facade.onboarding(ctx.store)}


@router.get("/seller/products", response_model=Envelope[Page[ProductView]])
async def products(
    ctx: Ctx,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    q: str = Query("", max_length=200),
    sale_mode: SaleMode | None = Query(None, alias="saleMode"),
):
    params = {"page": page, "pageSize": page_size, "q": q}
    if sale_mode:
        params["saleMode"] = sale_mode.value
    return {
        "data": await ctx.facade.products(
            ctx.store, await ctx.request.app.state.goods.list(ctx.store.id, **params)
        )
    }


@router.post("/products", response_model=Envelope[ProductView], status_code=201)
async def create_product(ctx: Ctx, payload: ProductCreate):
    return {
        "data": await ctx.facade.products(
            ctx.store, await ctx.request.app.state.goods.create(ctx.store.id, payload)
        )
    }


@router.get("/seller/products/{product_id}", response_model=Envelope[ProductView])
async def product(ctx: Ctx, product_id: UUID):
    return {
        "data": await ctx.facade.products(
            ctx.store, await ctx.request.app.state.goods.get(ctx.store.id, product_id)
        )
    }


@router.patch("/products/{product_id}", response_model=Envelope[ProductView])
async def update_product(ctx: Ctx, product_id: UUID, payload: ProductPatch):
    return {
        "data": await ctx.facade.products(
            ctx.store, await ctx.request.app.state.goods.update(ctx.store.id, product_id, payload)
        )
    }


@router.delete("/products/{product_id}", response_model=Envelope[dict])
async def delete_product(ctx: Ctx, product_id: UUID):
    await ctx.request.app.state.goods.delete(ctx.store.id, product_id)
    return {"data": None}


@router.get("/seller/posts", response_model=Envelope[Page[PostView]])
async def posts(
    ctx: Ctx,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    q: str = Query("", max_length=200),
    status: PostStatus | None = None,
):
    params = {"page": page, "pageSize": page_size, "q": q}
    if status:
        params["status"] = status.value
    return {
        "data": await ctx.facade.posts(
            ctx.store, await ctx.request.app.state.publications.list(ctx.store.id, **params)
        )
    }


@router.post("/posts", response_model=Envelope[PostView], status_code=201)
async def create_post(ctx: Ctx, payload: PostCreate):
    return {
        "data": await ctx.facade.posts(
            ctx.store, await ctx.request.app.state.publications.create(ctx.store.id, payload)
        )
    }


@router.get("/seller/posts/{post_id}", response_model=Envelope[PostView])
@router.get("/seller/posts/{post_id}/preview", response_model=Envelope[PostView])
async def post(ctx: Ctx, post_id: UUID):
    return {
        "data": await ctx.facade.posts(
            ctx.store, await ctx.request.app.state.publications.get(ctx.store.id, post_id)
        )
    }


@router.patch("/posts/{post_id}", response_model=Envelope[PostView])
async def update_post(ctx: Ctx, post_id: UUID, payload: PostPatch):
    return {
        "data": await ctx.facade.posts(
            ctx.store,
            await ctx.request.app.state.publications.update(ctx.store.id, post_id, payload),
        )
    }


@router.post("/seller/posts/{post_id}/publish", response_model=Envelope[PostView])
async def publish_post(ctx: Ctx, post_id: UUID):
    return {
        "data": await ctx.facade.posts(
            ctx.store, await ctx.request.app.state.publications.publish(ctx.store.id, post_id)
        )
    }


@router.delete("/posts/{post_id}", response_model=Envelope[dict])
async def delete_post(ctx: Ctx, post_id: UUID):
    await ctx.request.app.state.publications.delete(ctx.store.id, post_id)
    return {"data": None}


@router.post("/uploads", response_model=Envelope[dict], status_code=201)
async def upload(ctx: Ctx, file: UploadFile):
    return {"data": await MediaService(ctx.svc.db, ctx.request.app.state).upload(ctx.store, file)}


@router.get("/plans", response_model=Envelope[list[dict]])
async def plans(request: Request, db: DB):
    return {"data": SubscriptionService(db, request.app.state).plans()}


@router.get("/seller/subscription", response_model=Envelope[Entitlements])
async def subscription(ctx: Ctx):
    return {"data": await ctx.svc.entitlement(ctx.store.id)}


@router.post("/plans/subscribe", response_model=Envelope[dict])
async def subscribe(ctx: Ctx, payload: Subscribe, key: Key):
    store = await ctx.svc.repo.by_id(ctx.store.id, lock=True)
    return {
        "data": await SubscriptionService(ctx.svc.db, ctx.request.app.state).initiate(store, key)
    }


@router.post("/seller/subscription/sync", response_model=Envelope[dict])
async def sync(ctx: Ctx):
    store = await ctx.svc.repo.by_id(ctx.store.id, lock=True)
    return {"data": await SubscriptionService(ctx.svc.db, ctx.request.app.state).sync(store)}


@router.get("/seller/dashboard", response_model=Envelope[dict])
async def dashboard(
    ctx: Ctx,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
):
    return {"data": await ctx.facade.dashboard(ctx.store, start, end)}


@router.get("/seller/followers", response_model=Envelope[dict])
async def followers(
    ctx: Ctx, page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100)
):
    return {
        "data": (
            await ctx.request.app.state.users.followers(ctx.store.id, page, page_size)
        ).model_dump(by_alias=True)
    }


@router.get("/seller/orders", response_model=Envelope[dict])
async def orders(
    ctx: Ctx,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    status: OrderStatus | None = None,
    q: str = Query("", max_length=100),
):
    return {
        "data": (await ctx.facade.orders(ctx.store, page, page_size, status, q)).model_dump(
            mode="json", by_alias=True
        )
    }


@router.get("/seller/orders/{order_id}", response_model=Envelope[dict])
async def order(ctx: Ctx, order_id: str = Path(pattern=r"^[a-zA-Z0-9_-]{1,100}$")):
    return {
        "data": (await ctx.facade.order(ctx.store, order_id)).model_dump(mode="json", by_alias=True)
    }


@router.patch("/seller/orders/{order_id}/status", response_model=Envelope[dict])
async def change_order(
    ctx: Ctx,
    payload: StatusChange,
    key: Key,
    order_id: str = Path(pattern=r"^[a-zA-Z0-9_-]{1,100}$"),
):
    return {
        "data": (await ctx.facade.change_order(ctx.store, order_id, payload, key)).model_dump(
            mode="json", by_alias=True
        )
    }


@router.get(
    "/internal/sellers/{seller_id}/entitlements",
    dependencies=[Depends(internal)],
    response_model=Envelope[Entitlements],
    tags=["Policy internal API"],
)
async def entitlement(request: Request, db: DB, seller_id: UUID):
    return {"data": await StoreService(db, request.app.state).entitlement(seller_id)}


class MediaCheck(DTO):
    url: str = Field(max_length=2000)


@router.post(
    "/internal/sellers/{seller_id}/media/validate",
    dependencies=[Depends(internal)],
    response_model=Envelope[dict],
    tags=["Policy internal API"],
)
async def validate_media(request: Request, db: DB, seller_id: UUID, payload: MediaCheck):
    await StoreService(db, request.app.state).validate_media(seller_id, payload.url)
    return {"data": {"valid": True}}


@router.post(
    "/internal/sellers/entitlements/batch",
    dependencies=[Depends(internal)],
    response_model=Envelope[list[Entitlements]],
    tags=["Policy internal API"],
)
async def entitlements_batch(request: Request, db: DB, payload: SellerBatch):
    return {"data": await StoreService(db, request.app.state).entitlement_batch(payload.ids)}


@router.post(
    "/internal/stores/batch",
    dependencies=[Depends(internal)],
    response_model=Envelope[list[StorePublic]],
    tags=["Store read API"],
)
async def public_store_batch(request: Request, db: DB, payload: SellerBatch):
    return {"data": await StoreService(db, request.app.state).public_batch(payload.ids)}
