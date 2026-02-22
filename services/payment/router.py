"""
services/payment/router.py
Razorpay payment integration: order creation, webhook verification,
escrow management, and pandit payouts.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.settings import settings
from shared.middleware.auth import get_current_user, require_admin
from shared.models.models import (
    Booking,
    BookingStatus,
    Payment,
    PaymentStatus,
    User,
)
from shared.schemas.schemas import (
    MessageResponse,
    PaymentInitiateRequest,
    PaymentInitiateResponse,
    PaymentResponse,
    PaymentVerifyRequest,
)

router = APIRouter(prefix="/payments", tags=["Payments"])


def get_razorpay_client():
    """Lazy import Razorpay client."""
    try:
        import razorpay
        return razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
    except ImportError:
        raise HTTPException(status_code=503, detail="Payment service unavailable")


# ── Initiate Payment ──────────────────────────────────────────

@router.post("/initiate", response_model=PaymentInitiateResponse)
async def initiate_payment(
    data: PaymentInitiateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create Razorpay order for a booking.
    Client uses order_id + key_id to open Razorpay checkout.
    """
    # Fetch booking
    result = await db.execute(
        select(Booking).where(
            Booking.id == data.booking_id,
            Booking.user_id == current_user.id,
        )
    )
    booking = result.scalar_one_or_none()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.status not in (BookingStatus.SLOT_LOCKED, BookingStatus.PAYMENT_PENDING):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot initiate payment for booking in '{booking.status.value}' state",
        )

    # Check for existing payment
    existing_payment = await db.execute(
        select(Payment).where(Payment.booking_id == booking.id)
    )
    existing = existing_payment.scalar_one_or_none()
    if existing and existing.status == PaymentStatus.CAPTURED:
        raise HTTPException(status_code=400, detail="Payment already completed")

    # Create Razorpay order
    rzp = get_razorpay_client()
    amount_paise = int(float(booking.total_amount) * 100)  # Razorpay needs paise

    try:
        rzp_order = rzp.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": str(booking.id),
            "notes": {
                "booking_number": booking.booking_number,
                "user_id": str(current_user.id),
            },
        })
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {str(e)}")

    # Create/update Payment record
    if existing:
        existing.razorpay_order_id = rzp_order["id"]
    else:
        payment = Payment(
            booking_id=booking.id,
            user_id=current_user.id,
            razorpay_order_id=rzp_order["id"],
            amount=booking.total_amount,
            platform_fee=booking.platform_fee,
            status=PaymentStatus.PENDING,
        )
        db.add(payment)

    # Update booking status
    booking.status = BookingStatus.PAYMENT_PENDING
    await db.commit()

    return PaymentInitiateResponse(
        razorpay_order_id=rzp_order["id"],
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        amount=amount_paise,
        currency="INR",
        booking_id=str(booking.id),
    )


# ── Verify Payment (called from client after checkout) ────────

@router.post("/verify", response_model=MessageResponse)
async def verify_payment(
    data: PaymentVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify Razorpay payment signature after client checkout.
    Transitions booking: PAYMENT_PENDING → AWAITING_PANDIT.
    """
    # Verify signature
    body = f"{data.razorpay_order_id}|{data.razorpay_payment_id}"
    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, data.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Update payment record
    payment_result = await db.execute(
        select(Payment).where(
            Payment.razorpay_order_id == data.razorpay_order_id
        )
    )
    payment = payment_result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")

    payment.razorpay_payment_id = data.razorpay_payment_id
    payment.razorpay_signature = data.razorpay_signature
    payment.status = PaymentStatus.CAPTURED
    payment.captured_at = datetime.now(timezone.utc)

    # Transition booking
    booking_result = await db.execute(
        select(Booking).where(Booking.id == data.booking_id)
    )
    booking = booking_result.scalar_one_or_none()
    if booking:
        booking.status = BookingStatus.AWAITING_PANDIT

        # Notify pandit (in production: via Kafka event)
        from shared.models.models import Notification, NotificationType, PanditProfile
        pandit_result = await db.execute(
            select(PanditProfile).where(PanditProfile.id == booking.pandit_id)
        )
        pandit = pandit_result.scalar_one_or_none()
        if pandit:
            db.add(Notification(
                user_id=pandit.user_id,
                booking_id=booking.id,
                type=NotificationType.BOOKING_CREATED,
                title="New Booking Request 🙏",
                body=f"You have a new paid booking request for {booking.scheduled_at.strftime('%d %b %Y')}. Please accept or decline.",
            ))

    await db.commit()
    return MessageResponse(message="Payment verified. Pandit has been notified.")


# ── Razorpay Webhook ──────────────────────────────────────────

@router.post("/webhook", include_in_schema=False)
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Razorpay webhook handler. Validates HMAC signature.
    Handles: payment.captured, payment.failed, refund.processed.
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # Validate webhook signature
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    import json
    payload = json.loads(body)
    event = payload.get("event")
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    rzp_order_id = entity.get("order_id")
    rzp_payment_id = entity.get("id")

    if not rzp_order_id:
        return {"status": "ignored"}

    payment_result = await db.execute(
        select(Payment).where(Payment.razorpay_order_id == rzp_order_id)
    )
    payment = payment_result.scalar_one_or_none()
    if not payment:
        return {"status": "not_found"}

    if event == "payment.captured":
        if payment.status != PaymentStatus.CAPTURED:
            payment.status = PaymentStatus.CAPTURED
            payment.razorpay_payment_id = rzp_payment_id
            payment.captured_at = datetime.now(timezone.utc)

    elif event == "payment.failed":
        payment.status = PaymentStatus.FAILED

        # Compensating transaction: release slot + cancel booking
        booking_result = await db.execute(
            select(Booking).where(Booking.id == payment.booking_id)
        )
        booking = booking_result.scalar_one_or_none()
        if booking and booking.status == BookingStatus.PAYMENT_PENDING:
            booking.status = BookingStatus.CANCELLED
            booking.cancellation_reason = "Payment failed"
            booking.cancelled_at = datetime.now(timezone.utc)

    elif event == "refund.processed":
        refund_entity = payload.get("payload", {}).get("refund", {}).get("entity", {})
        payment.status = PaymentStatus.REFUNDED
        payment.refund_id = refund_entity.get("id")
        payment.refund_amount = float(refund_entity.get("amount", 0)) / 100
        payment.refunded_at = datetime.now(timezone.utc)

    await db.commit()
    return {"status": "ok"}


# ── Refund ────────────────────────────────────────────────────

@router.post("/{payment_id}/refund", response_model=MessageResponse)
async def refund_payment(
    payment_id: UUID,
    amount: float = None,  # None = full refund
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-triggered manual refund via Razorpay."""
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status not in (PaymentStatus.CAPTURED,):
        raise HTTPException(status_code=400, detail="Payment not eligible for refund")

    if not payment.razorpay_payment_id:
        raise HTTPException(status_code=400, detail="No Razorpay payment ID found")

    rzp = get_razorpay_client()
    refund_amount_paise = int((amount or float(payment.amount)) * 100)

    try:
        refund = rzp.payment.refund(
            payment.razorpay_payment_id,
            {"amount": refund_amount_paise},
        )
        payment.status = PaymentStatus.REFUNDED if not amount else PaymentStatus.PARTIALLY_REFUNDED
        payment.refund_id = refund.get("id")
        payment.refund_amount = refund_amount_paise / 100
        payment.refunded_at = datetime.now(timezone.utc)
        await db.commit()

        return MessageResponse(message=f"Refund of ₹{refund_amount_paise/100} initiated successfully")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Refund failed: {str(e)}")


# ── Read Endpoints ────────────────────────────────────────────

@router.get("/me/history", response_model=list[PaymentResponse])
async def my_payment_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get authenticated user's payment history."""
    result = await db.execute(
        select(Payment)
        .where(Payment.user_id == current_user.id)
        .order_by(Payment.created_at.desc())
        .limit(50)
    )
    payments = result.scalars().all()
    return [PaymentResponse.model_validate(p) for p in payments]
