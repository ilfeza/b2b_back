"""Real broker tests. No fake producer/consumer or Kafka transport replacements."""

import asyncio
import os
import time
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient
from sqlalchemy import func, select

from marketplace.shared.messaging.models import tables_for
from marketplace.shared.messaging.schema import EVENTS
from scripts.kafka_init import provision
from tests.conftest import onboard, product, upload
from tests.test_events import outbox

pytestmark = pytest.mark.kafka


async def eventually(check, deadline_seconds=40):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Kafka operation did not complete within timeout")


async def received(app, event_id):
    inbox = tables_for(app.state.settings.service).inbox
    async with app.state.sessions() as db:
        return (
            await db.scalar(
                select(func.count()).select_from(inbox).where(inbox.c.event_id == event_id)
            )
            == 1
        )


@pytest.fixture
async def kafka_cluster(cluster, postgres_url):
    bootstrap = os.environ.get("TEST_KAFKA_BOOTSTRAP_SERVERS")
    if not bootstrap or not postgres_url:
        pytest.skip("Real Kafka tests require TEST_KAFKA_BOOTSTRAP_SERVERS and PostgreSQL")
    prefix = "test." + uuid4().hex
    for app in cluster.apps.values():
        cfg = app.state.settings
        cfg.kafka_bootstrap_servers = bootstrap
        for kind in ["sellers", "products", "publications", "orders"]:
            setattr(cfg, f"kafka_{kind}_topic", f"{prefix}.{kind}")
        for service in ["b2b", "goods", "publications"]:
            setattr(cfg, f"kafka_{service}_group_id", f"{prefix}.{service}")
            setattr(cfg, f"kafka_{service}_dlq_topic", f"{prefix}.dlq.{service}")
        cfg.kafka_poll_interval_seconds = 0.02
        cfg.kafka_retry_base_seconds = 0.05
        cfg.kafka_retry_max_seconds = 0.2
        cfg.kafka_delivery_timeout_seconds = 2
        cfg.kafka_shutdown_timeout_seconds = 2
    await provision(cluster.apps["b2b"].state.settings)
    try:
        yield cluster
    finally:
        for app in cluster.apps.values():
            await app.state.events.stop()
        admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
        try:
            await admin.start()
            await admin.delete_topics(
                [name for name in await admin.list_topics() if name.startswith(prefix)]
            )
        finally:
            await admin.close()


def start_all(cluster):
    for app in cluster.apps.values():
        app.state.settings.kafka_enabled = True
        app.state.events.start()


async def test_real_kafka_domain_events_duplicate_invalid_message_and_dlq(kafka_cluster):
    cluster = kafka_cluster
    start_all(cluster)
    await onboard(cluster.client)
    media = await upload(cluster.client)
    item = (await product(cluster.client, media["url"])).json()["data"]
    post = (await cluster.client.post("/api/posts", json={"title": "Draft"})).json()["data"]
    assert (
        await cluster.client.patch(
            f"/api/posts/{post['id']}",
            json={
                "title": "Publication",
                "body": "<p>Ready</p>",
                "status": "published",
                "productIds": [item["id"]],
            },
        )
    ).status_code == 200
    assert (
        await cluster.client.patch(f"/api/products/{item['id']}", json={"price": 1900})
    ).status_code == 200
    assert (
        await cluster.client.patch(f"/api/posts/{post['id']}", json={"title": "Updated"})
    ).status_code == 200
    assert (await cluster.client.delete(f"/api/products/{item['id']}")).status_code == 200
    assert (await cluster.client.delete(f"/api/posts/{post['id']}")).status_code == 200
    b2b = cluster.apps["b2b"]
    settings = b2b.state.settings
    for owner in ["goods", "publications"]:
        for row in await outbox(cluster.apps[owner]):

            async def check(event_id=row["event_id"]):
                return await received(b2b, event_id)

            await eventually(check)
    seller_event = (await outbox(b2b))[0]
    for owner in ["goods", "publications"]:

        async def check(app=cluster.apps[owner]):
            return await received(app, seller_event["event_id"])

        await eventually(check)

    # An independent actual consumer confirms the on-wire event envelope/key.
    observer = AIOKafkaConsumer(
        settings.kafka_products_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=None,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers, enable_idempotence=True
    )
    dlq = AIOKafkaConsumer(
        settings.kafka_b2b_dlq_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=None,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await observer.start()
    await producer.start()
    await dlq.start()
    try:
        async with asyncio.timeout(30):
            message = await observer.getone()
        event = EVENTS.validate_json(message.value)
        assert event.event_type == "product.created"
        assert message.key == item["id"].encode()
        await producer.send_and_wait(
            settings.kafka_products_topic, key=message.key, value=message.value, partition=0
        )
        await producer.send_and_wait(
            settings.kafka_products_topic,
            key=message.key,
            value=b'{"private":"DO_NOT_LOG"}',
            partition=0,
        )
        # A valid message on the same partition proves that poison does not block it.
        marker = event.model_copy(update={"event_id": uuid4()})
        marker_value = marker.model_dump_json(by_alias=True).encode()
        await producer.send_and_wait(
            settings.kafka_products_topic, key=message.key, value=marker_value, partition=0
        )

        async def check_marker():
            return await received(b2b, marker.event_id)

        await eventually(check_marker)
        async with asyncio.timeout(30):
            rejected = EVENTS.validate_json((await dlq.getone()).value)
        assert rejected.event_type == "message.rejected"
        assert rejected.payload.reason_code == "invalid_schema"
        inbox = tables_for("b2b").inbox
        async with b2b.state.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(inbox)
                    .where(inbox.c.event_id == event.event_id)
                )
                == 1
            )
            activity = tables_for("b2b").activity
            # Two DISTINCT creation event IDs (real domain event + marker), not three.
            assert (
                await db.scalar(
                    select(activity.c.processed_count).where(
                        activity.c.seller_id == event.payload.seller_id,
                        activity.c.event_type == "product.created",
                    )
                )
                == 2
            )
    finally:
        await observer.stop()
        await producer.stop()
        await dlq.stop()


async def test_real_broker_delivery_after_connection_restored_and_consumer_restarted(kafka_cluster):
    cluster = kafka_cluster
    real_bootstrap = cluster.apps["b2b"].state.settings.kafka_bootstrap_servers
    # Runtime startup is non-blocking with an unreachable Kafka endpoint.
    for app in cluster.apps.values():
        app.state.settings.kafka_bootstrap_servers = "127.0.0.1:1"
    start_all(cluster)
    await onboard(cluster.client)
    media = await upload(cluster.client)
    item = (await product(cluster.client, media["url"])).json()["data"]
    goods = cluster.apps["goods"]
    row = (await outbox(goods))[0]
    assert row["published_at"] is None
    for app in cluster.apps.values():
        app.state.settings.kafka_bootstrap_servers = real_bootstrap
    b2b = cluster.apps["b2b"]

    async def check():
        return await received(b2b, row["event_id"])

    await eventually(check)
    assert (await outbox(goods))[0]["published_at"] is not None
    await b2b.state.events.stop()
    assert (
        await cluster.client.patch(f"/api/products/{item['id']}", json={"price": 2100})
    ).status_code == 200
    updated = (await outbox(goods))[-1]
    b2b.state.events.start()

    async def updated_received():
        return await received(b2b, updated["event_id"])

    await eventually(updated_received)
    inbox = tables_for("b2b").inbox
    async with b2b.state.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(inbox).where(inbox.c.event_id == row["event_id"])
            )
            == 1
        )
