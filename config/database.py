"""
config/database.py
Async SQLAlchemy engine, session factory, and base model.
Uses asyncpg driver for PostgreSQL with PostGIS support.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from config.settings import settings


# ── Engine ────────────────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT,
    pool_pre_ping=True,          # Detect stale connections
    pool_recycle=3600,           # Recycle connections every hour
    echo=settings.DEBUG,         # Log SQL in debug mode
)

# ── Session Factory ───────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,      # Don't expire after commit (async-safe)
    autocommit=False,
    autoflush=False,
)


# ── Base Model ────────────────────────────────────────────────
class Base(DeclarativeBase):
    """All ORM models inherit from this."""
    pass


# ── Dependency ────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: yields an async database session.
    Auto-commits on success, rolls back on error.

    Usage:
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager version for use outside of FastAPI routes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables. Run during app startup."""
    async with engine.begin() as conn:
        # Enable PostGIS and uuid-ossp extensions
        await conn.execute(
            __import__("sqlalchemy").text(
                "CREATE EXTENSION IF NOT EXISTS postgis;"
                "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
            )
        )
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose engine. Run during app shutdown."""
    await engine.dispose()
