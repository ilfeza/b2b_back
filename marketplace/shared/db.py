from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import DateTime, MetaData, Uuid, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def metadata(schema: str):
    return MetaData(schema=schema, naming_convention=NAMING)


def utcnow():
    return datetime.now(UTC)


class Timestamps:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def configure_database(app, settings):
    options = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        options["execution_options"] = {"schema_translate_map": {settings.service: None}}
    app.state.engine = create_async_engine(settings.database_url, **options)
    app.state.sessions = async_sessionmaker(
        app.state.engine, expire_on_commit=False, info={"settings": settings}
    )


async def session(request: Request):
    async with request.app.state.sessions() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def paginate(db: AsyncSession, query, page: int, page_size: int, serializer):
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = (await db.scalars(query.offset((page - 1) * page_size).limit(page_size))).all()
    return {
        "items": [serializer(x) for x in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }
