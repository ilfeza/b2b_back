"""Idempotent provisioning of proposed topics on the local KRaft broker."""

import asyncio

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaError, TopicAlreadyExistsError

from marketplace.shared.config import Settings


async def provision(settings):
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers, request_timeout_ms=5000
    )
    try:
        await admin.start()
        existing = set(await admin.list_topics())
        topics = {
            settings.kafka_sellers_topic,
            settings.kafka_products_topic,
            settings.kafka_publications_topic,
            settings.kafka_b2b_dlq_topic,
            settings.kafka_goods_dlq_topic,
            settings.kafka_publications_dlq_topic,
        }
        if settings.kafka_orders_enabled:
            topics.add(settings.kafka_orders_topic)
        for topic in sorted(topics - existing):
            try:
                await admin.create_topics(
                    [
                        NewTopic(
                            name=topic,
                            num_partitions=3,
                            replication_factor=1,
                            topic_configs={"retention.ms": "604800000"},
                        )
                    ]
                )
            except TopicAlreadyExistsError:
                pass
        print(f"Kafka topics ready: {len(topics)}")
    finally:
        await admin.close()


async def main():
    settings = Settings()
    for attempt in range(60):
        try:
            await provision(settings)
            return
        except (KafkaError, OSError, TimeoutError):
            if attempt == 59:
                raise RuntimeError("Kafka topic provisioning failed") from None
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
