"""
tests/test_bookings.py
Tests for the full booking lifecycle using the Saga pattern:
create → payment → accept/decline → complete/cancel
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import Booking, BookingStatus, PanditProfile, Pooja, User
from tests.conftest import auth_headers


# ── Booking Creation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_booking_success(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
):
    """User can create a booking for a verified pandit."""
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    payload = {
        "pandit_id": str(pandit_profile.id),
        "pooja_id": str(pooja.id),
        "scheduled_at": scheduled_at,
        "address": {
            "line1": "123 Test Street",
            "city": "Varanasi",
            "state": "UP",
            "pincode": "221001",
        },
    }

    response = await client.post("/bookings", headers=auth_headers(user), json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == BookingStatus.SLOT_LOCKED.value
    assert data["booking_number"].startswith("PB-")
    assert "total_amount" in data


@pytest.mark.asyncio
async def test_create_booking_past_date_rejected(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
):
    """Booking in the past must be rejected with 422."""
    scheduled_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    payload = {
        "pandit_id": str(pandit_profile.id),
        "pooja_id": str(pooja.id),
        "scheduled_at": scheduled_at,
        "address": {"line1": "123 Road", "city": "Varanasi", "state": "UP", "pincode": "221001"},
    }

    response = await client.post("/bookings", headers=auth_headers(user), json=payload)
    assert response.status_code == 422  # Pydantic field_validator rejects past dates


@pytest.mark.asyncio
async def test_pandit_cannot_create_booking(
    client: AsyncClient,
    pandit_user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
):
    """Pandits cannot book other pandits."""
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    payload = {
        "pandit_id": str(pandit_profile.id),
        "pooja_id": str(pooja.id),
        "scheduled_at": scheduled_at,
        "address": {"line1": "123 Road", "city": "Varanasi", "state": "UP", "pincode": "221001"},
    }

    response = await client.post("/bookings", headers=auth_headers(pandit_user), json=payload)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_booking_unverified_pandit_rejected(
    client: AsyncClient,
    user: User,
    db: AsyncSession,
    pandit_user: User,
    pooja: Pooja,
):
    """Cannot book an unverified pandit."""
    from shared.models.models import VerificationStatus
    unverified = PanditProfile(
        id=uuid.uuid4(),
        user_id=pandit_user.id,
        city="Delhi",
        verification_status=VerificationStatus.PENDING,
        is_available=True,
        base_fee=1000,
    )
    db.add(unverified)
    await db.commit()

    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    payload = {
        "pandit_id": str(unverified.id),
        "pooja_id": str(pooja.id),
        "scheduled_at": scheduled_at,
        "address": {"line1": "123 Road", "city": "Delhi", "state": "Delhi", "pincode": "110001"},
    }

    response = await client.post("/bookings", headers=auth_headers(user), json=payload)
    assert response.status_code == 400


# ── Booking Retrieval ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_bookings_empty(client: AsyncClient, user: User):
    response = await client.get("/bookings", headers=auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_bookings_with_status_filter(
    client: AsyncClient, user: User, pandit_profile: PanditProfile, pooja: Pooja
):
    # Create a booking
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    await client.post(
        "/bookings",
        headers=auth_headers(user),
        json={
            "pandit_id": str(pandit_profile.id),
            "pooja_id": str(pooja.id),
            "scheduled_at": scheduled_at,
            "address": {"line1": "1 Road", "city": "Varanasi", "state": "UP", "pincode": "221001"},
        },
    )

    # Filter by SLOT_LOCKED
    response = await client.get(
        "/bookings",
        headers=auth_headers(user),
        params={"status": "SLOT_LOCKED"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(b["status"] == "SLOT_LOCKED" for b in items)


@pytest.mark.asyncio
async def test_get_booking_not_found(client: AsyncClient, user: User):
    response = await client.get(f"/bookings/{uuid.uuid4()}", headers=auth_headers(user))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_access_other_users_booking(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """A user cannot fetch another user's booking."""
    # Create a booking belonging to a different user
    other_user = User(
        id=uuid.uuid4(),
        email="other@test.com",
        name="Other User",
        oauth_provider="google",
        oauth_id="other_google_123",
        role="user",
        is_active=True,
    )
    db.add(other_user)
    await db.commit()

    booking = Booking(
        id=uuid.uuid4(),
        user_id=other_user.id,
        pandit_id=pandit_profile.id,
        pooja_id=pooja.id,
        booking_number="PB-TEST-XXXX",
        status=BookingStatus.SLOT_LOCKED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000,
        platform_fee=200,
        total_amount=2200,
        pandit_payout=1800,
        address={"line1": "1 Road"},
    )
    db.add(booking)
    await db.commit()

    response = await client.get(f"/bookings/{booking.id}", headers=auth_headers(user))
    assert response.status_code == 403


# ── Booking State Transitions ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pandit_accept_booking(
    client: AsyncClient,
    user: User,
    pandit_user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Pandit can accept a booking in AWAITING_PANDIT state."""
    booking = Booking(
        id=uuid.uuid4(),
        user_id=user.id,
        pandit_id=pandit_profile.id,
        pooja_id=pooja.id,
        booking_number="PB-2024-ACCPT",
        status=BookingStatus.AWAITING_PANDIT,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000,
        platform_fee=200,
        total_amount=2200,
        pandit_payout=1800,
        address={"line1": "1 Road"},
        accept_deadline=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add(booking)
    await db.commit()

    response = await client.post(
        f"/bookings/{booking.id}/accept",
        headers=auth_headers(pandit_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == BookingStatus.CONFIRMED.value


@pytest.mark.asyncio
async def test_pandit_decline_booking(
    client: AsyncClient,
    user: User,
    pandit_user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Pandit can decline a booking — triggers compensating transaction (refund)."""
    booking = Booking(
        id=uuid.uuid4(),
        user_id=user.id,
        pandit_id=pandit_profile.id,
        pooja_id=pooja.id,
        booking_number="PB-2024-DECLN",
        status=BookingStatus.AWAITING_PANDIT,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000,
        platform_fee=200,
        total_amount=2200,
        pandit_payout=1800,
        address={"line1": "1 Road"},
        accept_deadline=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add(booking)
    await db.commit()

    response = await client.post(
        f"/bookings/{booking.id}/decline",
        headers=auth_headers(pandit_user),
        json={"reason": "Not available due to personal emergency"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == BookingStatus.DECLINED.value


@pytest.mark.asyncio
async def test_user_cancel_booking(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """User can cancel a CONFIRMED booking."""
    booking = Booking(
        id=uuid.uuid4(),
        user_id=user.id,
        pandit_id=pandit_profile.id,
        pooja_id=pooja.id,
        booking_number="PB-2024-CANCL",
        status=BookingStatus.CONFIRMED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000,
        platform_fee=200,
        total_amount=2200,
        pandit_payout=1800,
        address={"line1": "1 Road"},
    )
    db.add(booking)
    await db.commit()

    response = await client.post(
        f"/bookings/{booking.id}/cancel",
        headers=auth_headers(user),
        json={"reason": "Plans changed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == BookingStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_wrong_pandit_cannot_accept(
    client: AsyncClient,
    pandit_user: User,
    user: User,
    pooja: Pooja,
    db: AsyncSession,
):
    """A pandit cannot accept a booking assigned to a different pandit."""
    # Create a different pandit
    from shared.models.models import VerificationStatus
    other_pandit_user = User(
        id=uuid.uuid4(), email="other_pandit@test.com", name="Other Pandit",
        oauth_provider="google", oauth_id="other_pandit_google", role="pandit", is_active=True,
    )
    db.add(other_pandit_user)
    other_profile = PanditProfile(
        id=uuid.uuid4(), user_id=other_pandit_user.id, city="Delhi",
        verification_status=VerificationStatus.VERIFIED, is_available=True, base_fee=1000,
    )
    db.add(other_profile)
    await db.commit()

    booking = Booking(
        id=uuid.uuid4(),
        user_id=user.id,
        pandit_id=other_profile.id,  # Assigned to OTHER pandit
        pooja_id=pooja.id,
        booking_number="PB-2024-WRONG",
        status=BookingStatus.AWAITING_PANDIT,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000, platform_fee=200, total_amount=2200, pandit_payout=1800,
        address={"line1": "1 Road"},
        accept_deadline=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add(booking)
    await db.commit()

    # pandit_user tries to accept a booking that belongs to other_profile
    response = await client.post(
        f"/bookings/{booking.id}/accept",
        headers=auth_headers(pandit_user),
    )
    assert response.status_code == 403
