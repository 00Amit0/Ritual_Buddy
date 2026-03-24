"""Background relay that publishes outbox events to Kafka/Redpanda."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer
from sqlalchemy import select

from config.database import AsyncSessionLocal
from config.settings import settings
from shared.models.models import OutboxEvent

logger = logging.getLogger(__name__)


async def publish_once(producer: AIOKafkaProducer, batch_size: int = 100) -> int:
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status.in_(["NEW", "FAILED"]), OutboxEvent.available_at <= now)
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
        )
        events = result.scalars().all()

        published = 0
        for evt in events:
            try:
                headers = [(k, str(v).encode("utf-8")) for k, v in (evt.headers or {}).items()]
                await producer.send_and_wait(
                    evt.topic,
                    key=evt.event_key.encode("utf-8"),
                    value=json.dumps(evt.payload).encode("utf-8"),
                    headers=headers,
                )
                evt.status = "PUBLISHED"
                evt.published_at = datetime.now(timezone.utc)
                evt.last_error = None
                published += 1
            except Exception as exc:  # noqa: BLE001
                evt.status = "FAILED"
                evt.attempts += 1
                evt.last_error = str(exc)

        await db.commit()
        return published


async def run_relay() -> None:
    if not settings.EVENT_BUS_ENABLED:
        logger.warning("EVENT_BUS_ENABLED=false; outbox relay not started")
        return

    producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
    await producer.start()
    logger.info("Outbox relay connected to %s", settings.KAFKA_BOOTSTRAP_SERVERS)
    try:
        while True:
            count = await publish_once(producer)
            if count == 0:
                await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_SECONDS)
    finally:
        await producer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_relay())
