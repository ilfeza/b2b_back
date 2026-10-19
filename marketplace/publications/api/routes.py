from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from marketplace.publications.schemas.posts import PostCreate, PostPatch, PostRead
from marketplace.publications.services.posts import PostsService
from marketplace.shared.db import session
from marketplace.shared.schemas import Envelope, Page, PostStatus
from marketplace.shared.security import internal

router = APIRouter(dependencies=[Depends(internal)], tags=["Publications internal API"])
DB = Annotated[AsyncSession, Depends(session, scope="function")]


def service(request: Request, db: DB):
    return PostsService(db, request.app.state.policy, request.app.state.goods)


Service = Annotated[PostsService, Depends(service)]


@router.get("/internal/sellers/{seller_id}/posts", response_model=Envelope[Page[PostRead]])
async def posts(
    seller_id: UUID,
    svc: Service,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    q: str = Query("", max_length=200),
    status: PostStatus | None = None,
):
    return {"data": await svc.list(seller_id, page, page_size, q, status)}


@router.post(
    "/internal/sellers/{seller_id}/posts", response_model=Envelope[PostRead], status_code=201
)
async def create(seller_id: UUID, payload: PostCreate, svc: Service):
    return {"data": await svc.create(seller_id, payload)}


@router.get("/internal/sellers/{seller_id}/posts/{post_id}", response_model=Envelope[PostRead])
async def get(seller_id: UUID, post_id: UUID, svc: Service):
    return {"data": await svc.get(seller_id, post_id)}


@router.patch("/internal/sellers/{seller_id}/posts/{post_id}", response_model=Envelope[PostRead])
async def update(seller_id: UUID, post_id: UUID, payload: PostPatch, svc: Service):
    return {"data": await svc.update(seller_id, post_id, payload)}


@router.post(
    "/internal/sellers/{seller_id}/posts/{post_id}/publish", response_model=Envelope[PostRead]
)
async def publish(seller_id: UUID, post_id: UUID, svc: Service):
    return {"data": await svc.update(seller_id, post_id, PostPatch(status=PostStatus.PUBLISHED))}


@router.delete("/internal/sellers/{seller_id}/posts/{post_id}", response_model=Envelope[dict])
async def delete(seller_id: UUID, post_id: UUID, svc: Service):
    await svc.delete(seller_id, post_id)
    return {"data": None}


@router.get("/internal/posts", response_model=Envelope[Page[PostRead]])
async def read_published(
    svc: Service,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    q: str = Query("", max_length=200),
    seller_id: UUID | None = Query(None, alias="sellerId"),
):
    return {"data": await svc.read_published(page, page_size, q, seller_id)}


@router.get("/internal/posts/{post_id}", response_model=Envelope[PostRead])
async def read_one(post_id: UUID, svc: Service):
    return {"data": await svc.read_one(post_id)}


@router.get("/internal/sellers/{seller_id}/media/references", response_model=Envelope[list[str]])
async def media_references(seller_id: UUID, svc: Service):
    return {"data": await svc.media_references(seller_id)}
