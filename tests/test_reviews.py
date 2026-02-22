"""
tests/test_reviews.py
Tests for review creation, rating validation, flagging, and admin moderation.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import Booking, BookingStatus, PanditProfile, Pooja, User
from tests.conftest import auth_headers


def _make_booking(user_id, pandit_id, pooja_id, status=BookingStatus.COMPLETED):
    return Booking(
        id=uuid.uuid4(),
        user_id=user_id,
        pandit_id=pandit_id,
        pooja_id=pooja_id,
        booking_number=f"PB-REV-{uuid.uuid4().hex[:6].upper()}",
        status=status,
        scheduled_at=datetime.now(timezone.utc) - timedelta(days=1),
        base_amount=2000,
        platform_fee=200,
        total_amount=2200,
        pandit_payout=1800,
        address={"line1": "1 Road"},
        completed_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )


@pytest.mark.asyncio
async def test_create_review_success(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """User can review a COMPLETED booking."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    response = await client.post(
        "/reviews",
        headers=auth_headers(user),
        json={
            "booking_id": str(booking.id),
            "rating": 5,
            "comment": "Excellent service, very knowledgeable pandit!",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["rating"] == 5
    assert data["comment"] == "Excellent service, very knowledgeable pandit!"


@pytest.mark.asyncio
async def test_cannot_review_non_completed_booking(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Cannot review a booking that is not yet COMPLETED."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id, status=BookingStatus.CONFIRMED)
    db.add(booking)
    await db.commit()

    response = await client.post(
        "/reviews",
        headers=auth_headers(user),
        json={"booking_id": str(booking.id), "rating": 4, "comment": "Trying to review early"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rating_must_be_1_to_5(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Rating outside 1–5 range is rejected with 422."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    for invalid_rating in [0, 6, -1]:
        response = await client.post(
            "/reviews",
            headers=auth_headers(user),
            json={"booking_id": str(booking.id), "rating": invalid_rating, "comment": "Test"},
        )
        assert response.status_code == 422, f"Expected 422 for rating={invalid_rating}"


@pytest.mark.asyncio
async def test_cannot_review_twice(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Second review for the same booking returns 409."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    payload = {"booking_id": str(booking.id), "rating": 5, "comment": "Great!"}

    r1 = await client.post("/reviews", headers=auth_headers(user), json=payload)
    assert r1.status_code == 201

    r2 = await client.post("/reviews", headers=auth_headers(user), json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_cannot_review_others_booking(
    client: AsyncClient,
    user: User,
    pandit_user: User,  # Different user
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """User cannot review a booking that belongs to another user."""
    # Create booking owned by pandit_user, not user
    booking = _make_booking(pandit_user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    response = await client.post(
        "/reviews",
        headers=auth_headers(user),  # user, not pandit_user
        json={"booking_id": str(booking.id), "rating": 3, "comment": "Trying to steal review"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_flag_review(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Anyone can flag a review without authentication."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    # Create review first
    r = await client.post(
        "/reviews",
        headers=auth_headers(user),
        json={"booking_id": str(booking.id), "rating": 1, "comment": "Terrible!"},
    )
    review_id = r.json()["id"]

    # Flag without auth (public endpoint)
    flag_response = await client.put(
        f"/reviews/{review_id}/flag",
        params={"reason": "This review contains false information"},
    )
    assert flag_response.status_code == 200


@pytest.mark.asyncio
async def test_admin_delete_review(
    client: AsyncClient,
    user: User,
    admin_user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Admin can soft-delete (hide) a review."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    r = await client.post(
        "/reviews",
        headers=auth_headers(user),
        json={"booking_id": str(booking.id), "rating": 1, "comment": "Bad review to delete"},
    )
    review_id = r.json()["id"]

    delete_response = await client.delete(
        f"/reviews/{review_id}",
        headers=auth_headers(admin_user),
    )
    assert delete_response.status_code == 200


@pytest.mark.asyncio
async def test_non_admin_cannot_delete_review(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Regular users cannot delete reviews."""
    booking = _make_booking(user.id, pandit_profile.id, pooja.id)
    db.add(booking)
    await db.commit()

    r = await client.post(
        "/reviews",
        headers=auth_headers(user),
        json={"booking_id": str(booking.id), "rating": 5, "comment": "Good"},
    )
    review_id = r.json()["id"]

    response = await client.delete(f"/reviews/{review_id}", headers=auth_headers(user))
    assert response.status_code == 403
