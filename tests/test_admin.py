"""
tests/test_admin.py
Tests for admin-only endpoints: pandit verification, user moderation, analytics, audit log.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import (
    Booking, BookingStatus, PanditProfile, Payment, PaymentStatus,
    Pooja, User, UserRole, VerificationStatus,
)
from tests.conftest import auth_headers


# ── Access Control ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_cannot_access_admin_endpoints(client: AsyncClient, user: User):
    """Regular users get 403 on all admin endpoints."""
    response = await client.get("/admin/pandits/pending", headers=auth_headers(user))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_pandit_cannot_access_admin_endpoints(client: AsyncClient, pandit_user: User):
    """Pandits get 403 on admin endpoints."""
    response = await client.get("/admin/analytics", headers=auth_headers(pandit_user))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_access_admin(client: AsyncClient):
    response = await client.get("/admin/pandits/pending")
    assert response.status_code == 401


# ── Pandit Verification Queue ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_pending_pandits_empty(client: AsyncClient, admin_user: User):
    """Empty queue returns empty list."""
    response = await client.get("/admin/pandits/pending", headers=auth_headers(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_pending_pandits_with_data(
    client: AsyncClient,
    admin_user: User,
    pandit_user: User,
    db: AsyncSession,
):
    """Pending pandits appear in the verification queue."""
    pending = PanditProfile(
        id=uuid.uuid4(),
        user_id=pandit_user.id,
        city="Varanasi",
        state="UP",
        experience_years=5,
        verification_status=VerificationStatus.PENDING,
        is_available=False,
        base_fee=1500,
    )
    db.add(pending)
    await db.commit()

    response = await client.get("/admin/pandits/pending", headers=auth_headers(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["pandit_id"] == str(pending.id)


# ── Pandit Verification ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_verify_pandit(
    client: AsyncClient,
    admin_user: User,
    pandit_user: User,
    db: AsyncSession,
):
    """Admin can verify a pending pandit."""
    pending = PanditProfile(
        id=uuid.uuid4(),
        user_id=pandit_user.id,
        city="Varanasi",
        verification_status=VerificationStatus.PENDING,
        is_available=False,
        base_fee=1500,
    )
    db.add(pending)
    await db.commit()

    response = await client.post(
        f"/admin/pandits/{pending.id}/verify",
        headers=auth_headers(admin_user),
        json={"notes": "All documents verified. Approved."},
    )
    assert response.status_code == 200

    # Confirm status changed in DB
    await db.refresh(pending)
    assert pending.verification_status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_admin_reject_pandit(
    client: AsyncClient,
    admin_user: User,
    pandit_user: User,
    db: AsyncSession,
):
    """Admin can reject a pandit application with a reason."""
    pending = PanditProfile(
        id=uuid.uuid4(),
        user_id=pandit_user.id,
        city="Mumbai",
        verification_status=VerificationStatus.PENDING,
        is_available=False,
        base_fee=1000,
    )
    db.add(pending)
    await db.commit()

    response = await client.post(
        f"/admin/pandits/{pending.id}/reject",
        headers=auth_headers(admin_user),
        json={"reason": "Incomplete documentation — please re-upload certificates."},
    )
    assert response.status_code == 200

    await db.refresh(pending)
    assert pending.verification_status == VerificationStatus.REJECTED


@pytest.mark.asyncio
async def test_admin_suspend_pandit(
    client: AsyncClient,
    admin_user: User,
    pandit_profile: PanditProfile,
    db: AsyncSession,
):
    """Admin can suspend a verified pandit."""
    response = await client.post(
        f"/admin/pandits/{pandit_profile.id}/suspend",
        headers=auth_headers(admin_user),
        json={"reason": "Multiple user complaints", "duration_days": 30},
    )
    assert response.status_code == 200

    await db.refresh(pandit_profile)
    assert pandit_profile.verification_status == VerificationStatus.SUSPENDED
    assert pandit_profile.is_available is False


@pytest.mark.asyncio
async def test_verify_nonexistent_pandit_returns_404(client: AsyncClient, admin_user: User):
    response = await client.post(
        f"/admin/pandits/{uuid.uuid4()}/verify",
        headers=auth_headers(admin_user),
        json={"notes": ""},
    )
    assert response.status_code == 404


# ── User Moderation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_suspend_user(
    client: AsyncClient,
    admin_user: User,
    user: User,
    db: AsyncSession,
):
    """Admin can suspend a regular user."""
    response = await client.post(
        f"/admin/users/{user.id}/suspend",
        headers=auth_headers(admin_user),
        json={"reason": "Fraudulent activity detected"},
    )
    assert response.status_code == 200

    await db.refresh(user)
    assert user.is_active is False


@pytest.mark.asyncio
async def test_admin_cannot_suspend_another_admin(
    client: AsyncClient,
    admin_user: User,
    db: AsyncSession,
):
    """Admins cannot be suspended."""
    another_admin = User(
        id=uuid.uuid4(),
        email="admin2@test.com",
        name="Admin 2",
        oauth_provider="google",
        oauth_id="admin2_google",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(another_admin)
    await db.commit()

    response = await client.post(
        f"/admin/users/{another_admin.id}/suspend",
        headers=auth_headers(admin_user),
        json={"reason": "Testing"},
    )
    assert response.status_code == 403


# ── Analytics ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_analytics(client: AsyncClient, admin_user: User):
    """Analytics endpoint returns expected fields."""
    response = await client.get("/admin/analytics", headers=auth_headers(admin_user))
    assert response.status_code == 200
    data = response.json()

    expected_fields = [
        "total_users", "total_pandits", "verified_pandits",
        "pending_verification", "total_bookings", "bookings_today",
        "total_revenue", "revenue_today", "avg_rating",
    ]
    for field in expected_fields:
        assert field in data, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_analytics_counts_correctly(
    client: AsyncClient,
    admin_user: User,
    user: User,
    pandit_profile: PanditProfile,
):
    """Analytics returns accurate counts for seeded data."""
    response = await client.get("/admin/analytics", headers=auth_headers(admin_user))
    data = response.json()

    assert data["total_users"] >= 1  # at least `user` fixture
    assert data["total_pandits"] >= 1  # at least `pandit_profile` fixture
    assert data["verified_pandits"] >= 1  # pandit_profile is VERIFIED


# ── Admin Booking Overview ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_list_all_bookings(
    client: AsyncClient,
    admin_user: User,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Admin can view all bookings across all users."""
    booking = Booking(
        id=uuid.uuid4(),
        user_id=user.id,
        pandit_id=pandit_profile.id,
        pooja_id=pooja.id,
        booking_number="PB-ADMIN-TEST",
        status=BookingStatus.CONFIRMED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000, platform_fee=200, total_amount=2200, pandit_payout=1800,
        address={"line1": "1 Road"},
    )
    db.add(booking)
    await db.commit()

    response = await client.get("/admin/bookings", headers=auth_headers(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_admin_filter_bookings_by_status(
    client: AsyncClient,
    admin_user: User,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    booking = Booking(
        id=uuid.uuid4(), user_id=user.id, pandit_id=pandit_profile.id, pooja_id=pooja.id,
        booking_number="PB-FILTER-TEST", status=BookingStatus.COMPLETED,
        scheduled_at=datetime.now(timezone.utc) - timedelta(days=1),
        base_amount=2000, platform_fee=200, total_amount=2200, pandit_payout=1800,
        address={"line1": "1 Road"},
        completed_at=datetime.now(timezone.utc),
    )
    db.add(booking)
    await db.commit()

    response = await client.get(
        "/admin/bookings",
        headers=auth_headers(admin_user),
        params={"status_filter": "COMPLETED"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(b["status"] == "COMPLETED" for b in items)


# ── Audit Log ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_log_is_populated_after_action(
    client: AsyncClient,
    admin_user: User,
    pandit_user: User,
    db: AsyncSession,
):
    """Verifying a pandit creates an audit log entry."""
    pending = PanditProfile(
        id=uuid.uuid4(), user_id=pandit_user.id, city="Delhi",
        verification_status=VerificationStatus.PENDING, is_available=False, base_fee=1000,
    )
    db.add(pending)
    await db.commit()

    # Perform action
    await client.post(
        f"/admin/pandits/{pending.id}/verify",
        headers=auth_headers(admin_user),
        json={"notes": "Approved"},
    )

    # Check audit log
    response = await client.get("/admin/audit-logs", headers=auth_headers(admin_user))
    assert response.status_code == 200
    logs = response.json()["items"]
    assert any(log["action"] == "VERIFY_PANDIT" for log in logs)
