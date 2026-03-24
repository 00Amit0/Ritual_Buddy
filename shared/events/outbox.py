"""Transactional outbox helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import OutboxEvent
from shared.events.schemas import build_event


async def enqueue_event(
    db: AsyncSession,
    *,
    topic: str,
    event_type: str,
    event_key: str,
    payload: dict[str, Any],
    headers: dict[str, Any] | None = None,
) -> None:
    event = OutboxEvent(
        topic=topic,
        event_type=event_type,
        event_key=event_key,
        payload=build_event(event_type=event_type, payload=payload),
        headers=headers or {},
        status="NEW",
    )
    db.add(event)


def enqueue_event_sync(
    db: Session,
    *,
    topic: str,
    event_type: str,
    event_key: str,
    payload: dict[str, Any],
    headers: dict[str, Any] | None = None,
) -> None:
    event = OutboxEvent(
        topic=topic,
        event_type=event_type,
        event_key=event_key,
        payload=build_event(event_type=event_type, payload=payload),
        headers=headers or {},
        status="NEW",
    )
    db.add(event)
