"""
tests/test_payments.py
Tests for payment initiation, HMAC signature verification, and webhook processing.
"""

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import (
    Booking, BookingStatus, PanditProfile, Payment, PaymentStatus, Pooja, User,
)
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_initiate_payment_requires_auth(client: AsyncClient):
    response = await client.post("/payments/initiate", json={"booking_id": str(uuid.uuid4())})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_initiate_payment_booking_not_found(client: AsyncClient, user: User):
    response = await client.post(
        "/payments/initiate",
        headers=auth_headers(user),
        json={"booking_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_initiate_payment_success(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    """Initiating payment for a SLOT_LOCKED booking calls Razorpay and returns order_id."""
    booking = Booking(
        id=uuid.uuid4(),
        user_id=user.id,
        pandit_id=pandit_profile.id,
        pooja_id=pooja.id,
        booking_number="PB-2024-PAYMT",
        status=BookingStatus.SLOT_LOCKED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000, platform_fee=200, total_amount=2200, pandit_payout=1800,
        address={"line1": "1 Road"},
        accept_deadline=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add(booking)
    await db.commit()

    # Mock Razorpay client
    mock_order = {"id": "order_test_123", "amount": 220000, "currency": "INR"}
    with patch("services.payment.router.razorpay.Client") as mock_rzp:
        mock_rzp.return_value.order.create.return_value = mock_order

        response = await client.post(
            "/payments/initiate",
            headers=auth_headers(user),
            json={"booking_id": str(booking.id)},
        )

    assert response.status_code == 200
    data = response.json()
    assert "razorpay_order_id" in data or "order_id" in data


@pytest.mark.asyncio
async def test_payment_history_empty(client: AsyncClient, user: User):
    response = await client.get("/payments/me/history", headers=auth_headers(user))
    assert response.status_code == 200
    assert response.json() == [] or isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_payment_history_with_records(
    client: AsyncClient,
    user: User,
    pandit_profile: PanditProfile,
    pooja: Pooja,
    db: AsyncSession,
):
    booking = Booking(
        id=uuid.uuid4(), user_id=user.id, pandit_id=pandit_profile.id, pooja_id=pooja.id,
        booking_number="PB-TEST-HIST", status=BookingStatus.CONFIRMED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        base_amount=2000, platform_fee=200, total_amount=2200, pandit_payout=1800,
        address={"line1": "1 Road"},
    )
    db.add(booking)
    payment = Payment(
        id=uuid.uuid4(), booking_id=booking.id, user_id=user.id,
        razorpay_order_id="order_hist_123",
        amount=2200, currency="INR", platform_fee=200,
        status=PaymentStatus.CAPTURED,
    )
    db.add(payment)
    await db.commit()

    response = await client.get("/payments/me/history", headers=auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["amount"] == "2200" or data[0]["amount"] == 2200


@pytest.mark.asyncio
async def test_razorpay_signature_verification():
    """Unit test: HMAC signature verification logic."""
    from shared.utils.security import verify_razorpay_signature

    order_id = "order_test_abc"
    payment_id = "pay_test_xyz"
    key_secret = "test_secret_key"

    # Generate a valid signature
    message = f"{order_id}|{payment_id}"
    valid_signature = hmac.new(
        key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    # Should pass with correct signature
    assert verify_razorpay_signature(order_id, payment_id, valid_signature, key_secret)

    # Should fail with tampered signature
    assert not verify_razorpay_signature(order_id, payment_id, "bad_signature", key_secret)

    # Should fail with wrong order_id
    assert not verify_razorpay_signature("wrong_order", payment_id, valid_signature, key_secret)


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected(client: AsyncClient):
    """Webhook requests with invalid HMAC signature return 400."""
    response = await client.post(
        "/payments/webhook",
        headers={"X-Razorpay-Signature": "invalid_signature"},
        content=b'{"event": "payment.captured"}',
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_refund_requires_admin(client: AsyncClient, user: User):
    """Only admins can trigger manual refunds."""
    response = await client.post(
        f"/payments/{uuid.uuid4()}/refund",
        headers=auth_headers(user),
        json={"reason": "Customer request"},
    )
    assert response.status_code == 403
