import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from marketplace.shared.messaging.models import tables_for
from marketplace.shared.messaging.runtime import KafkaRuntime
from marketplace.shared.messaging.schema import EVENTS
from tests.conftest import onboard, product


def record(event, settings, *, offset=0, value=None, topic=None, key=None):
    return SimpleNamespace(
        topic=topic or settings.kafka_products_topic,
        partition=0,
        offset=offset,
        key=key if key is not None else event.aggregate_id.encode(),
        value=value if value is not None else event.model_dump_json(by_alias=True).encode(),
    )


def product_event():
    identity = uuid4()
    return EVENTS.validate_python(
        {
            "eventId": str(uuid4()),
            "eventType": "product.created",
            "version": 1,
            "occurredAt": "2026-10-19T12:00:00Z",
            "aggregateId": str(identity),
            "payload": {"productId": str(identity), "sellerId": str(uuid4())},
        }
    )


async def outbox(app):
    table = tables_for(app.state.settings.service).outbox
    async with app.state.sessions() as db:
        return (await db.execute(select(table).order_by(table.c.sequence))).mappings().all()


async def test_domain_changes_record_all_seven_event_types_once(cluster, seller):
    store, media = seller
    await onboard(cluster.client)  # Idempotent onboarding must not emit seller.created twice.
    item = (await product(cluster.client, media["url"])).json()["data"]
    assert (
        await cluster.client.patch(f"/api/products/{item['id']}", json={"price": 2000})
    ).status_code == 200
    draft = (await cluster.client.post("/api/posts", json={"title": "Draft"})).json()["data"]
    assert not await outbox(cluster.apps["publications"])
    assert (
        await cluster.client.patch(
            f"/api/posts/{draft['id']}",
            json={
                "body": "<p>Ready</p>",
                "status": "published",
                "productIds": [item["id"]],
            },
        )
    ).status_code == 200
    assert (
        await cluster.client.patch(f"/api/posts/{draft['id']}", json={"title": "Changed"})
    ).status_code == 200
    assert (await cluster.client.delete(f"/api/posts/{draft['id']}")).status_code == 200
    assert (await cluster.client.delete(f"/api/products/{item['id']}")).status_code == 200
    expected = {
        "b2b": ["seller.created"],
        "goods": ["product.created", "product.updated", "product.deleted"],
        "publications": ["publication.published", "publication.updated", "publication.deleted"],
    }
    for service, types in expected.items():
        rows = await outbox(cluster.apps[service])
        assert [row["event_type"] for row in rows] == types
        assert all(row["published_at"] is None for row in rows)
        for row in rows:
            event = EVENTS.validate_python(row["message"])
            assert str(event.payload.seller_id) == store["id"]


async def test_outbox_insert_failure_rolls_back_product(cluster, seller, monkeypatch):
    async def fail(*args):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr("marketplace.shared.messaging.outbox.save_event", fail)
    response = await product(cluster.client, seller[1]["url"])
    assert response.status_code == 503  # Goods 500 is normalized by the typed upstream client.
    assert (await cluster.client.get("/api/seller/products")).json()["data"]["total"] == 0
    assert not await outbox(cluster.apps["goods"])


async def test_consumer_inbox_and_effect_are_idempotent(cluster):
    app = cluster.apps["b2b"]
    processor = app.state.events.processor
    event = product_event()
    message = record(event, app.state.settings)
    assert await processor.process(message) == "processed"
    assert await processor.process(message) == "duplicate"
    table = tables_for("b2b").activity
    async with app.state.sessions() as db:
        assert await db.scalar(select(table.c.processed_count)) == 1
    # Same event ID with changed payload is a conflict, never a second effect.
    altered = event.model_copy(
        update={"payload": event.payload.model_copy(update={"seller_id": uuid4()})}
    )
    await app.state.events.process_record(record(altered, app.state.settings, offset=1))
    assert [r["event_type"] for r in await outbox(app)] == ["message.rejected"]


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_json",
        "unknown_version",
        "boolean_version",
        "naive_timestamp",
        "wrong_aggregate",
        "wrong_key",
        "wrong_topic",
        "unknown_type",
        "oversized",
    ],
)
async def test_bad_message_is_durable_dlq_and_no_secret_in_logs(cluster, caplog, mutation):
    app = cluster.apps["b2b"]
    event = product_event()
    body = event.model_dump(mode="json", by_alias=True)
    kwargs = {}
    if mutation == "invalid_json":
        kwargs["value"] = b"PRIVATE_TOKEN_NOT_FOR_LOGS"
    elif mutation == "unknown_version":
        body["version"] = 999
    elif mutation == "boolean_version":
        body["version"] = True
    elif mutation == "naive_timestamp":
        body["occurredAt"] = "2026-10-19T12:00:00"
    elif mutation == "wrong_aggregate":
        body["aggregateId"] = str(uuid4())
    elif mutation == "unknown_type":
        body["eventType"] = "product.unknown"
    elif mutation == "wrong_key":
        kwargs["key"] = b"other"
    elif mutation == "wrong_topic":
        kwargs["topic"] = app.state.settings.kafka_sellers_topic
    elif mutation == "oversized":
        kwargs["value"] = b"a" * (app.state.settings.kafka_max_event_bytes + 1)
    kwargs.setdefault("value", json.dumps(body).encode())
    message = record(event, app.state.settings, **kwargs)
    assert await app.state.events.process_record(message)
    assert await app.state.events.process_record(message)
    rows = await outbox(app)
    assert len(rows) == 1
    rejected = EVENTS.validate_python(rows[0]["message"])
    assert rejected.event_type == "message.rejected"
    assert rejected.payload.truncated == (mutation == "oversized")
    assert "PRIVATE_TOKEN_NOT_FOR_LOGS" not in caplog.text
    tables = tables_for("b2b")
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(tables.inbox)) == 0
        assert await db.scalar(select(tables.failures.c.status)) == "dead_letter"


async def test_consumer_retries_handler_then_recovers_atomically(cluster, monkeypatch):
    runtime = cluster.apps["b2b"].state.events
    runtime.settings.kafka_retry_base_seconds = 0.001
    runtime.settings.kafka_retry_max_seconds = 0.001
    original = runtime.processor.apply
    calls = 0

    async def fail_twice(db, event):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("sensitive message must not be logged")
        await original(db, event)

    monkeypatch.setattr(runtime.processor, "apply", fail_twice)
    assert await runtime.process_record(record(product_event(), runtime.settings))
    async with runtime.sessions() as db:
        assert await db.scalar(select(runtime.tables.activity.c.processed_count)) == 1
        assert await db.scalar(select(runtime.tables.failures.c.status)) == "recovered"
    assert calls == 3 and not await outbox(cluster.apps["b2b"])


async def test_permanent_handler_failure_dead_letters_after_retries(cluster, monkeypatch):
    runtime = cluster.apps["b2b"].state.events
    runtime.settings.kafka_retry_base_seconds = 0.001
    runtime.settings.kafka_retry_max_seconds = 0.001

    async def fail(db, event):
        raise RuntimeError("private")

    monkeypatch.setattr(runtime.processor, "apply", fail)
    assert await runtime.process_record(record(product_event(), runtime.settings))
    rows = await outbox(cluster.apps["b2b"])
    assert rows[0]["message"]["payload"]["attempts"] == 3
    async with runtime.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(runtime.tables.inbox)) == 0


async def test_database_outage_never_dead_letters_or_commits_event(cluster, monkeypatch):
    runtime = cluster.apps["b2b"].state.events
    runtime.settings.kafka_retry_base_seconds = 0.001
    runtime.settings.kafka_retry_max_seconds = 0.001
    original = runtime.processor.process
    calls = 0

    async def recover(message):
        nonlocal calls
        calls += 1
        if calls < 5:
            raise OperationalError("unavailable", {}, Exception())
        return await original(message)

    monkeypatch.setattr(runtime.processor, "process", recover)
    assert await runtime.process_record(record(product_event(), runtime.settings))
    assert calls == 5
    async with runtime.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(runtime.tables.failures)) == 0


async def test_outbox_retains_event_when_broker_is_unavailable_then_delivers(cluster, seller):
    app = cluster.apps["goods"]
    await product(cluster.client, seller[1]["url"])
    runtime = app.state.events
    runtime.settings.kafka_retry_base_seconds = 0.001
    runtime.settings.kafka_retry_max_seconds = 0.001

    class Producer:
        unavailable = True
        messages = []

        async def send_and_wait(self, topic, *, key, value):
            if self.unavailable:
                raise KafkaConnectionError()
            self.messages.append((topic, key, value))

    producer = Producer()
    assert not await runtime.dispatch_once(producer)
    row = (await outbox(app))[0]
    assert row["published_at"] is None and row["attempts"] == 1
    assert not await runtime.dispatch_once(producer)  # Respect durable retry deadline.
    producer.unavailable = False
    await asyncio.sleep(0.01)
    assert await runtime.dispatch_once(producer)
    assert not await runtime.dispatch_once(producer)
    assert len(producer.messages) == 1
    assert (await outbox(app))[0]["event_id"] == row["event_id"]
    assert (await outbox(app))[0]["published_at"] is not None


async def test_external_order_events_are_opt_in_and_do_not_store_orders(cluster):
    runtime = cluster.apps["b2b"].state.events
    event = EVENTS.validate_python(
        {
            "eventId": str(uuid4()),
            "eventType": "order.status_changed",
            "version": 1,
            "occurredAt": "2026-10-19T12:00:00Z",
            "aggregateId": "order-1",
            "payload": {
                "orderId": "order-1",
                "sellerId": str(uuid4()),
                "previousStatus": "paid",
                "status": "shipped",
            },
        }
    )
    message = record(event, runtime.settings, topic=runtime.settings.kafka_orders_topic)
    with pytest.raises(ValueError):
        runtime.processor.validate(message)
    runtime.settings.kafka_orders_enabled = True
    assert await runtime.processor.process(message) == "processed"
    assert not any(t.name == "orders" for t in runtime.tables.inbox.metadata.tables.values())


async def test_event_operations_api_is_internal_only(cluster, seller):
    assert (await cluster.client.get("/api/internal/events")).status_code == 401
    from tests.conftest import KEY

    headers = {"X-Service-Key": KEY}
    response = await cluster.client.get("/api/internal/events", headers=headers)
    assert response.status_code == 200
    event_id = response.json()["data"]["outbox"][0]["event_id"]
    response = await cluster.client.get(f"/api/internal/events/outbox/{event_id}", headers=headers)
    assert response.json()["data"]["eventType"] == "seller.created"
    assert (
        await cluster.client.get(f"/api/internal/events/outbox/{uuid4()}", headers=headers)
    ).status_code == 404


async def test_duplicate_delivery_concurrent_transactions_have_one_effect(cluster):
    runtime = cluster.apps["b2b"].state.events
    message = record(product_event(), runtime.settings)
    results = await asyncio.gather(*(runtime.processor.process(message) for _ in range(4)))
    assert sorted(results) == ["duplicate", "duplicate", "duplicate", "processed"]
    async with runtime.sessions() as db:
        assert await db.scalar(select(runtime.tables.activity.c.processed_count)) == 1


@pytest.mark.postgres
async def test_two_publishers_use_database_lock_and_publish_once(cluster, seller, postgres_url):
    if not postgres_url:
        pytest.skip("Publisher coordination requires PostgreSQL advisory locks")
    app = cluster.apps["goods"]
    await product(cluster.client, seller[1]["url"])
    runtime = app.state.events
    second = KafkaRuntime(runtime.settings, runtime.sessions)

    class Producer:
        calls = 0

        async def send_and_wait(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(0.05)

    producer = Producer()
    results = await asyncio.gather(runtime.dispatch_once(producer), second.dispatch_once(producer))
    assert sorted(results) == [False, True]
    assert producer.calls == 1


async def test_broker_ack_then_database_commit_failure_replays_same_event_id(cluster, seller):
    from sqlalchemy import event

    app = cluster.apps["goods"]
    await product(cluster.client, seller[1]["url"])
    runtime = app.state.events

    class Producer:
        messages = []

        async def send_and_wait(self, topic, *, key, value):
            self.messages.append(value)

    def fail_commit(connection):
        raise RuntimeError("simulated DB commit failure after broker ack")

    producer = Producer()
    engine = app.state.engine.sync_engine
    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError):
            await runtime.dispatch_once(producer)
    finally:
        event.remove(engine, "commit", fail_commit)
    assert (await outbox(app))[0]["published_at"] is None
    assert await runtime.dispatch_once(producer)
    assert len(producer.messages) == 2
    assert (
        json.loads(producer.messages[0])["eventId"] == json.loads(producer.messages[1])["eventId"]
    )


@pytest.mark.parametrize("kind", ["producer", "consumer"])
async def test_kafka_clients_force_close_when_graceful_shutdown_times_out(
    cluster, monkeypatch, kind
):
    runtime = cluster.apps["b2b"].state.events
    runtime.settings.kafka_shutdown_timeout_seconds = 0.01
    client = (AIOKafkaProducer if kind == "producer" else AIOKafkaConsumer)(
        bootstrap_servers="127.0.0.1:1"
    )
    connection = client.client if kind == "producer" else client._client
    closed = False
    original = connection.close

    async def hang():
        await asyncio.Event().wait()

    async def close_connection():
        nonlocal closed
        closed = True
        await original()

    monkeypatch.setattr(client, "stop", hang)
    monkeypatch.setattr(connection, "close", close_connection)
    async with asyncio.timeout(3):
        await runtime.close(client)
    assert closed and client._closed


@pytest.mark.postgres
async def test_concurrent_onboarding_emits_one_seller_created(cluster, postgres_url, monkeypatch):
    if not postgres_url:
        pytest.skip("Activation serialization requires PostgreSQL row locks")
    users = cluster.apps["b2b"].state.users
    original = users.ensure_seller

    async def slow_confirmation(*args):
        # Other requests read the pending reservation before this activates it.
        await asyncio.sleep(0.03)
        return await original(*args)

    monkeypatch.setattr(users, "ensure_seller", slow_confirmation)
    stores = await asyncio.gather(
        *(onboard(cluster.client, "concurrent_event_shop") for _ in range(4))
    )
    assert len({store["id"] for store in stores}) == 1
    assert [r["event_type"] for r in await outbox(cluster.apps["b2b"])] == ["seller.created"]
