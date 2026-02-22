"""
config/redis_client.py
Async Redis client for caching, slot locking, JWT deny-list,
geo queries, and pub/sub (Socket.io adapter).
"""

import json
from typing import Any, Optional
import redis.asyncio as aioredis

from config.settings import settings


# ── Global client (initialized on startup) ───────────────────
redis_client: Optional[aioredis.Redis] = None


async def init_redis() -> None:
    """Initialize the Redis connection pool."""
    global redis_client
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
    )
    # Test connection
    await redis_client.ping()


async def close_redis() -> None:
    """Close Redis connection pool."""
    global redis_client
    if redis_client:
        await redis_client.aclose()


def get_redis() -> aioredis.Redis:
    """FastAPI dependency to get Redis client."""
    if not redis_client:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return redis_client


# ── Cache Helpers ─────────────────────────────────────────────
class RedisCache:
    """Helper class for common Redis caching patterns."""

    def __init__(self, client: aioredis.Redis):
        self.client = client

    async def get(self, key: str) -> Optional[Any]:
        value = await self.client.get(key)
        if value:
            return json.loads(value)
        return None

    async def set(self, key: str, value: Any, ttl: int = settings.REDIS_CACHE_TTL) -> None:
        await self.client.setex(key, ttl, json.dumps(value, default=str))

    async def delete(self, key: str) -> None:
        await self.client.delete(key)

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern. Use carefully in production."""
        keys = await self.client.keys(pattern)
        if keys:
            return await self.client.delete(*keys)
        return 0

    # ── Slot Locking ─────────────────────────────────────────
    async def lock_slot(self, pandit_id: str, slot_datetime: str, booking_id: str) -> bool:
        """
        Atomic slot lock using SET NX (set if not exists).
        Returns True if lock acquired, False if slot already locked.
        """
        key = f"slot_lock:{pandit_id}:{slot_datetime}"
        result = await self.client.set(
            key,
            booking_id,
            ex=settings.REDIS_SLOT_LOCK_TTL,
            nx=True,  # Only set if key doesn't exist
        )
        return result is True

    async def release_slot(self, pandit_id: str, slot_datetime: str) -> None:
        key = f"slot_lock:{pandit_id}:{slot_datetime}"
        await self.client.delete(key)

    async def get_slot_lock(self, pandit_id: str, slot_datetime: str) -> Optional[str]:
        key = f"slot_lock:{pandit_id}:{slot_datetime}"
        return await self.client.get(key)

    # ── JWT Deny List ─────────────────────────────────────────
    async def revoke_token(self, jti: str, ttl_seconds: int) -> None:
        """Add JWT ID to deny list until it expires."""
        await self.client.setex(f"jwt_revoked:{jti}", ttl_seconds, "1")

    async def is_token_revoked(self, jti: str) -> bool:
        return await self.client.exists(f"jwt_revoked:{jti}") == 1

    # ── Geo Queries (Real-time nearby pandits) ────────────────
    async def add_pandit_location(self, pandit_id: str, lng: float, lat: float) -> None:
        """Update pandit's real-time location in Redis GEO set."""
        await self.client.geoadd("pandits_geo", [lng, lat, pandit_id])

    async def get_nearby_pandits(
        self,
        lat: float,
        lng: float,
        radius_km: float,
        count: int = 50,
    ) -> list[dict]:
        """Get pandit IDs within radius_km of given coordinates."""
        results = await self.client.georadiusbymember(
            "pandits_geo",
            lng, lat,
            radius_km,
            unit="km",
            withcoord=True,
            withdist=True,
            count=count,
            sort="ASC",
        )
        return [
            {"pandit_id": r[0], "distance_km": float(r[1]), "coords": r[2]}
            for r in results
        ]

    # ── Rate Limiting ─────────────────────────────────────────
    async def check_rate_limit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        """
        Sliding window rate limiter.
        Returns True if request is allowed, False if rate limited.
        """
        pipe = self.client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        current_count = results[0]
        return current_count <= limit
