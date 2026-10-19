from uuid import uuid4

from marketplace.shared.db import utcnow
from marketplace.shared.messaging.models import tables_for
from marketplace.shared.messaging.schema import EVENTS

OWNED = {
    "b2b": {"seller.created"},
    "goods": {"product.created", "product.updated", "product.deleted"},
    "publications": {"publication.published", "publication.updated", "publication.deleted"},
}


async def enqueue(db, event_type: str, aggregate_id, payload: dict):
    settings = db.info["settings"]
    if event_type not in OWNED[settings.service]:
        raise ValueError("Event type does not belong to this producer")
    event = EVENTS.validate_python(
        {
            "eventId": uuid4(),
            "eventType": event_type,
            "version": 1,
            "occurredAt": utcnow(),
            "aggregateId": str(aggregate_id),
            "payload": payload,
        }
    )
    await save_event(db, settings.kafka_topic_for(event_type), event)
    return event


async def save_event(db, topic, event):
    # No commit: the caller's domain change and this INSERT must succeed together.
    await db.execute(
        tables_for(db.info["settings"].service)
        .outbox.insert()
        .values(
            event_id=event.event_id,
            topic=topic,
            event_type=event.event_type,
            aggregate_id=event.aggregate_id,
            message=event.model_dump(mode="json", by_alias=True),
        )
    )
