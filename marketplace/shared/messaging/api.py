from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from marketplace.shared.db import session
from marketplace.shared.errors import missing
from marketplace.shared.messaging.models import tables_for
from marketplace.shared.security import internal

router = APIRouter(tags=["Event operations"], dependencies=[Depends(internal)])


@router.get("/internal/events")
async def status(request: Request, limit: int = Query(20, ge=1, le=100), db=Depends(session)):
    settings = request.app.state.settings
    tables = tables_for(settings.service)
    runtime = request.app.state.events
    pending = await db.scalar(
        select(func.count())
        .select_from(tables.outbox)
        .where(tables.outbox.c.published_at.is_(None))
    )
    published = await db.scalar(
        select(func.count())
        .select_from(tables.outbox)
        .where(tables.outbox.c.published_at.is_not(None))
    )
    inbox = (
        (
            await db.execute(
                select(tables.inbox).order_by(tables.inbox.c.processed_at.desc()).limit(limit)
            )
        )
        .mappings()
        .all()
    )
    activity = (
        (
            await db.execute(
                select(tables.activity)
                .order_by(tables.activity.c.last_occurred_at.desc())
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    failures = await db.scalar(
        select(func.count())
        .select_from(tables.failures)
        .where(tables.failures.c.status == "dead_letter")
    )
    outbox = (
        (
            await db.execute(
                select(
                    tables.outbox.c.event_id,
                    tables.outbox.c.event_type,
                    tables.outbox.c.aggregate_id,
                    tables.outbox.c.topic,
                    tables.outbox.c.published_at,
                    tables.outbox.c.attempts,
                )
                .order_by(tables.outbox.c.sequence.desc())
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    return {
        "data": {
            "enabled": settings.kafka_enabled,
            "consumerGroup": settings.kafka_group_id,
            "subscriptions": sorted(settings.kafka_subscriptions),
            "producerConnected": runtime.producer_connected,
            "consumerConnected": runtime.consumer_connected,
            "outboxPending": pending,
            "outboxPublished": published,
            "deadLetters": failures,
            "outbox": [dict(r) for r in outbox],
            "receipts": [dict(r) for r in inbox],
            "activity": [dict(r) for r in activity],
        },
        "error": None,
    }


@router.get("/internal/events/outbox/{event_id}")
async def outbox_event(request: Request, event_id: UUID, db=Depends(session)):
    table = tables_for(request.app.state.settings.service).outbox
    event = await db.scalar(select(table.c.message).where(table.c.event_id == event_id))
    if event is None:
        missing()
    return {"data": event, "error": None}
