"""
tests/conftest.py
Shared pytest fixtures for async API/service tests.
"""

import uuid
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import AsyncSessionLocal, Base
from config.redis_client import close_redis, init_redis
from main import app
from shared.models.models import (
    OAuthProvider,
    PanditProfile,
    Pooja,
    PoojaCategory,
    User,
    UserRole,
    VerificationStatus,
)
from shared.utils.security import create_access_token


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the full test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def auth_headers(user: User) -> dict:
    """Return Authorization header for a persisted user."""
    token, _ = create_access_token(
        user_id=str(user.id),
        role=user.role.value,
        email=user.email,
    )
    return {"Authorization": f"Bearer {token}"}


async def _truncate_all_tables(session: AsyncSession) -> None:
    """Hard reset test data between tests, preserving schema/migrations."""
    table_names = [t.name for t in Base.metadata.sorted_tables]
    if not table_names:
        return
    joined = ", ".join(f'"{name}"' for name in table_names)
    await session.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _reset_db() -> AsyncGenerator[None, None]:
    """Reset all rows for every test, even if test does not use `db` fixture."""
    async with AsyncSessionLocal() as session:
        await _truncate_all_tables(session)
    yield
    async with AsyncSessionLocal() as session:
        await _truncate_all_tables(session)


@pytest_asyncio.fixture
async def db(_reset_db) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    await init_redis()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_redis()


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        oauth_provider=OAuthProvider.GOOGLE,
        oauth_id=f"user-{uuid.uuid4()}",
        email=f"user-{uuid.uuid4()}@test.com",
        name="Test User",
        role=UserRole.USER,
        is_active=True,
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def pandit_user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        oauth_provider=OAuthProvider.GOOGLE,
        oauth_id=f"pandit-{uuid.uuid4()}",
        email=f"pandit-{uuid.uuid4()}@test.com",
        name="Test Pandit",
        role=UserRole.PANDIT,
        is_active=True,
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        oauth_provider=OAuthProvider.GOOGLE,
        oauth_id=f"admin-{uuid.uuid4()}",
        email=f"admin-{uuid.uuid4()}@test.com",
        name="Test Admin",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def pooja(db: AsyncSession) -> Pooja:
    p = Pooja(
        id=uuid.uuid4(),
        name_en="Ganesh Pooja",
        name_hi="गणेश पूजा",
        slug=f"ganesh-pooja-{uuid.uuid4().hex[:8]}",
        category=PoojaCategory.GRIHA,
        is_active=True,
    )
    db.add(p)
    await db.commit()
    return p


@pytest_asyncio.fixture
async def pandit_profile(db: AsyncSession, pandit_user: User) -> PanditProfile:
    profile = PanditProfile(
        id=uuid.uuid4(),
        user_id=pandit_user.id,
        bio="Experienced pandit",
        experience_years=8,
        city="Varanasi",
        state="UP",
        pincode="221001",
        verification_status=VerificationStatus.VERIFIED,
        is_available=True,
        base_fee=1500,
        profile_complete=True,
    )
    db.add(profile)
    await db.commit()
    return profile
