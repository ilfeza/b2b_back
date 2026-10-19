from __future__ import annotations

import base64
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from marketplace.shared.db import utcnow
from marketplace.shared.messaging.models import tables_for
from marketplace.shared.messaging.outbox import save_event
from marketplace.shared.messaging.schema import EVENTS, MessageRejected


class InvalidEvent(ValueError):
    """Safe reason code; never include raw message data in logs."""


def insert_for(db, table):
    return (pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert)(table)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class EventProcessor:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions
        self.tables = tables_for(settings.service)

    def validate(self, record):
        if record.value is None or len(record.value) > self.settings.kafka_max_event_bytes:
            raise InvalidEvent("invalid_size")
        event = EVENTS.validate_json(record.value)
        allowed = self.settings.kafka_subscriptions.get(record.topic, set())
        if event.event_type not in allowed:
            raise InvalidEvent("unexpected_topic_or_type")
        if record.key != event.aggregate_id.encode():
            raise InvalidEvent("aggregate_key_mismatch")
        return event

    async def process(self, record):
        event = self.validate(record)
        canonical = json.dumps(event.model_dump(mode="json", by_alias=True), sort_keys=True)
        message_hash = digest(canonical.encode())
        group = self.settings.kafka_group_id
        inbox = self.tables.inbox
        async with self.sessions.begin() as db:
            inserted = await db.scalar(
                insert_for(db, inbox)
                .values(
                    consumer_group=group,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    aggregate_id=event.aggregate_id,
                    seller_id=event.payload.seller_id,
                    occurred_at=event.occurred_at,
                    message_hash=message_hash,
                    topic=record.topic,
                    partition=record.partition,
                    offset=record.offset,
                )
                .on_conflict_do_nothing()
                .returning(inbox.c.event_id)
            )
            if inserted is None:
                previous = await db.scalar(
                    select(inbox.c.message_hash).where(
                        inbox.c.consumer_group == group, inbox.c.event_id == event.event_id
                    )
                )
                if previous != message_hash:
                    raise InvalidEvent("event_id_collision")
                return "duplicate"
            await self.apply(db, event)
            await db.execute(
                self.tables.failures.update()
                .where(
                    self.tables.failures.c.consumer_group == group,
                    self.tables.failures.c.topic == record.topic,
                    self.tables.failures.c.partition == record.partition,
                    self.tables.failures.c.offset == record.offset,
                    self.tables.failures.c.status == "retry",
                )
                .values(status="recovered", updated_at=utcnow())
            )
        return "processed"

    async def apply(self, db, event):
        # Durable, inspectable activity counters, NOT catalog/post/order replicas.
        # This state is never read by authorization, entitlements or quota checks.
        activity = self.tables.activity
        await db.execute(
            insert_for(db, activity)
            .values(
                consumer_group=self.settings.kafka_group_id,
                seller_id=event.payload.seller_id,
                event_type=event.event_type,
                processed_count=1,
                last_occurred_at=event.occurred_at,
            )
            .on_conflict_do_update(
                index_elements=[
                    activity.c.consumer_group,
                    activity.c.seller_id,
                    activity.c.event_type,
                ],
                set_={
                    "processed_count": activity.c.processed_count + 1,
                    "last_occurred_at": case(
                        (activity.c.last_occurred_at < event.occurred_at, event.occurred_at),
                        else_=activity.c.last_occurred_at,
                    ),
                },
            )
        )

    async def failure(self, record, reason_code: str, *, permanent: bool):
        """Persist attempts and DLQ outbox atomically BEFORE committing source offset."""
        failures = self.tables.failures
        group = self.settings.kafka_group_id
        identity = dict(
            consumer_group=group,
            topic=record.topic,
            partition=record.partition,
            offset=record.offset,
        )
        value = record.value or b""
        async with self.sessions.begin() as db:
            await db.execute(
                insert_for(db, failures)
                .values(
                    **identity,
                    attempts=0,
                    reason_code=reason_code,
                    status="retry",
                    original_sha256=digest(value),
                )
                .on_conflict_do_nothing()
            )
            query = select(failures).filter_by(**identity).with_for_update()
            row = (await db.execute(query)).mappings().one()
            if row["status"] == "dead_letter":
                return True
            attempts = row["attempts"] + 1
            dead = permanent or attempts >= self.settings.kafka_handler_max_attempts
            await db.execute(
                failures.update()
                .filter_by(**identity)
                .values(
                    attempts=attempts,
                    reason_code=reason_code,
                    status="dead_letter" if dead else "retry",
                    updated_at=utcnow(),
                )
            )
            if dead:
                rejection_id = uuid5(
                    NAMESPACE_URL, f"{group}:{record.topic}:{record.partition}:{record.offset}"
                )
                event = MessageRejected(
                    event_id=rejection_id,
                    event_type="message.rejected",
                    version=1,
                    occurred_at=utcnow(),
                    aggregate_id=str(rejection_id),
                    payload={
                        "consumerGroup": group,
                        "originalTopic": record.topic,
                        "originalPartition": record.partition,
                        "originalOffset": record.offset,
                        "reasonCode": reason_code,
                        "attempts": attempts,
                        "originalSha256": digest(value),
                        "originalSize": len(value),
                        "originalValueBase64": base64.b64encode(value[:4096]).decode(),
                        "truncated": len(value) > 4096,
                    },
                )
                await save_event(db, self.settings.kafka_dlq_topic, event)
            return dead
