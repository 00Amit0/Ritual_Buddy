"""Event definitions and helper builders for domain events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid


def build_event(event_type: str, payload: dict[str, Any], version: int = 1) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "version": version,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
