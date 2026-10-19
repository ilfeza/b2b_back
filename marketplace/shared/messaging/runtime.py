from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, timedelta

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from marketplace.shared.db import utcnow
from marketplace.shared.messaging.consumer import EventProcessor, InvalidEvent
from marketplace.shared.messaging.models import tables_for

logger = logging.getLogger("marketplace")


class KafkaRuntime:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions
        self.tables = tables_for(settings.service)
        self.processor = EventProcessor(settings, sessions)
        self.tasks = []
        self.stopping = asyncio.Event()
        self.producer_connected = False
        self.consumer_connected = False

    def log(self, name, error=None):
        # No str(error), payload, credentials, SQL parameters or connection URLs.
        logger.info(
            name,
            extra={
                "service": self.settings.service,
                "type": type(error).__name__ if error else None,
            },
        )

    def start(self):
        if self.settings.kafka_enabled and not self.tasks:
            self.stopping.clear()
            self.tasks = [
                asyncio.create_task(self.publish_loop(), name="kafka-outbox"),
                asyncio.create_task(self.consume_loop(), name="kafka-consumer"),
            ]

    async def stop(self):
        self.stopping.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    def backoff(self, attempts):
        return min(
            self.settings.kafka_retry_max_seconds,
            self.settings.kafka_retry_base_seconds * 2 ** min(attempts - 1, 10),
        )

    async def pause(self, delay):
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stopping.wait(), timeout=delay)

    async def close(self, client):
        try:
            await asyncio.wait_for(client.stop(), self.settings.kafka_shutdown_timeout_seconds)
        except (Exception, asyncio.CancelledError) as error:
            self.log("kafka_client_close_interrupted", error)
            # aiokafka 0.13 uses different transport attributes on producer/consumer.
            # Close network requests before finalizers, even with Kafka unreachable.
            connection = client.client if isinstance(client, AIOKafkaProducer) else client._client
            await connection.close()
            components = (
                [client._sender]
                if isinstance(client, AIOKafkaProducer)
                else [client._coordinator, client._fetcher]
            )
            for component in components:
                if component is not None:
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await asyncio.wait_for(component.close(), 1)
            client._closed = True

    async def dispatch_once(self, producer):
        outbox = self.tables.outbox
        async with self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                # One publisher per service schema, across all API worker replicas.
                lock_id = {"b2b": 703001, "goods": 703002, "publications": 703003}[
                    self.settings.service
                ]
                if not await db.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": lock_id}
                ):
                    return False
            row = (
                (
                    await db.execute(
                        select(outbox)
                        .where(outbox.c.published_at.is_(None))
                        .order_by(outbox.c.sequence)
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            due = row["next_attempt_at"]
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if due > utcnow():
                return False
            try:
                import json

                async with asyncio.timeout(self.settings.kafka_delivery_timeout_seconds):
                    await producer.send_and_wait(
                        row["topic"],
                        key=row["aggregate_id"].encode(),
                        value=json.dumps(row["message"], separators=(",", ":")).encode(),
                    )
            except Exception as error:
                attempts = row["attempts"] + 1
                await db.execute(
                    outbox.update()
                    .where(outbox.c.sequence == row["sequence"])
                    .values(
                        attempts=attempts,
                        last_error=type(error).__name__[:100],
                        next_attempt_at=utcnow() + timedelta(seconds=self.backoff(attempts)),
                    )
                )
                self.log("kafka_outbox_delivery_retry", error)
                return False
            await db.execute(
                outbox.update()
                .where(outbox.c.sequence == row["sequence"])
                .values(
                    published_at=utcnow(),
                    last_error=None,
                )
            )
        self.log("kafka_outbox_delivered")
        return True

    async def publish_loop(self):
        while not self.stopping.is_set():
            producer = AIOKafkaProducer(
                bootstrap_servers=self.settings.kafka_bootstrap_servers,
                client_id=f"{self.settings.service}-outbox",
                enable_idempotence=True,
                acks="all",
                request_timeout_ms=3000,
                max_request_size=1048576,
            )
            try:
                await asyncio.wait_for(
                    producer.start(), self.settings.kafka_delivery_timeout_seconds
                )
                self.producer_connected = True
                while not self.stopping.is_set():
                    if not await self.dispatch_once(producer):
                        await self.pause(self.settings.kafka_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.log("kafka_publisher_retry", error)
            finally:
                self.producer_connected = False
                await self.close(producer)
            await self.pause(self.settings.kafka_retry_base_seconds)

    async def process_record(self, record):
        attempts = 0
        while not self.stopping.is_set():
            try:
                result = await self.processor.process(record)
                self.log(f"kafka_event_{result}")
                return True
            except (ValidationError, InvalidEvent) as error:
                # Pydantic errors may contain the input, so only a fixed code is stored/logged.
                reason = str(error) if isinstance(error, InvalidEvent) else "invalid_schema"
                await self.processor.failure(record, reason, permanent=True)
                self.log("kafka_event_dead_lettered")
                return True
            except (SQLAlchemyError, TimeoutError, OSError) as error:
                # Database failures are infrastructure failures: never skip the event.
                self.log("kafka_consumer_database_retry", error)
            except Exception as error:
                self.log("kafka_handler_retry", error)
                if await self.processor.failure(
                    record, type(error).__name__[:100], permanent=False
                ):
                    return True
            attempts += 1
            await self.pause(self.backoff(attempts))
        return False

    async def consume_loop(self):
        while not self.stopping.is_set():
            consumer = AIOKafkaConsumer(
                *self.settings.kafka_subscriptions,
                bootstrap_servers=self.settings.kafka_bootstrap_servers,
                client_id=f"{self.settings.service}-events",
                group_id=self.settings.kafka_group_id,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                request_timeout_ms=15000,
                max_poll_interval_ms=300000,
            )
            try:
                await asyncio.wait_for(
                    consumer.start(), self.settings.kafka_delivery_timeout_seconds
                )
                self.consumer_connected = True
                async for record in consumer:
                    if await self.process_record(record):
                        # DB commit (inbox + effects or durable DLQ outbox) happens first.
                        await consumer.commit(
                            {TopicPartition(record.topic, record.partition): record.offset + 1}
                        )
                    else:
                        break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Includes rebalance/commit failures: replay is safe because of the inbox.
                self.log("kafka_consumer_retry", error)
            finally:
                self.consumer_connected = False
                await self.close(consumer)
            await self.pause(self.settings.kafka_retry_base_seconds)
