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
from shared.events.outbox import enqueue_event
from shared.middleware.auth import (
    get_current_principal,
)
from shared.models.models import (
    Payment,
    PaymentBookingProjection,
    PaymentStatus,
    UserRole,
)
from shared.schemas.schemas import (
    MessageResponse,
    PaymentInitiateRequest,
    PaymentInitiateResponse,
    PaymentResponse,
    PaymentVerifyRequest,
)
from shared.utils.security import verify_razorpay_webhook_signature
from shared.utils.third_party import get_razorpay_client as build_razorpay_client

router = APIRouter(prefix="/payments", tags=["Payments"])


def get_razorpay_client():
    """Lazy Razorpay client with explicit configuration check."""
    try:
        return build_razorpay_client()
    except ImportError:
        raise HTTPException(status_code=503, detail="Payment service unavailable")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── Initiate Payment ──────────────────────────────────────────

@router.post("/initiate", response_model=PaymentInitiateResponse)
async def initiate_payment(
    data: PaymentInitiateRequest,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """
    Create Razorpay order for a booking.
    Client uses order_id + key_id to open Razorpay checkout.
    """
    booking = await db.get(PaymentBookingProjection, data.booking_id)
    current_user_id = UUID(str(current_user.id))

    if not booking or booking.user_id != current_user_id:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.status not in ("SLOT_LOCKED", "PAYMENT_PENDING"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot initiate payment for booking in '{booking.status}' state",
        )

    # Check for existing payment
    existing_payment = await db.execute(
        select(Payment).where(Payment.booking_id == booking.booking_id)
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
            "receipt": str(booking.booking_id),
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
        payment = existing
    else:
        payment = Payment(
            booking_id=booking.booking_id,
            user_id=current_user_id,
            razorpay_order_id=rzp_order["id"],
            amount=booking.total_amount,
            platform_fee=booking.platform_fee,
            status=PaymentStatus.PENDING,
        )
        db.add(payment)

    await db.flush()
    await enqueue_event(
        db,
        topic="payment-events",
        event_type="payment.initiated",
        event_key=str(payment.id),
        payload={
            "payment_id": str(payment.id),
            "booking_id": str(booking.booking_id),
            "booking_number": booking.booking_number,
            "user_id": str(booking.user_id),
            "pandit_id": str(booking.pandit_id),
            "amount": float(booking.total_amount),
        },
    )
    await db.commit()

    return PaymentInitiateResponse(
        razorpay_order_id=rzp_order["id"],
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        amount=amount_paise,
        currency="INR",
        booking_id=str(booking.booking_id),
    )


# ── Verify Payment (called from client after checkout) ────────

@router.post("/verify", response_model=MessageResponse)
async def verify_payment(
    data: PaymentVerifyRequest,
    current_user=Depends(get_current_principal),
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
    current_user_id = UUID(str(current_user.id))
    if payment.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    payment.razorpay_payment_id = data.razorpay_payment_id
    payment.razorpay_signature = data.razorpay_signature
    payment.status = PaymentStatus.CAPTURED
    payment.captured_at = datetime.now(timezone.utc)

    booking = await db.get(PaymentBookingProjection, data.booking_id)

    if booking:
        await enqueue_event(
            db,
            topic="payment-events",
            event_type="payment.captured",
            event_key=str(payment.id),
            payload={
                "payment_id": str(payment.id),
                "booking_id": str(booking.booking_id),
                "booking_number": booking.booking_number,
                "user_id": str(booking.user_id),
                "pandit_id": str(booking.pandit_id),
                "amount": float(payment.amount),
            },
        )
    await db.commit()

    return MessageResponse(message="Payment verified successfully.")


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
    if not verify_razorpay_webhook_signature(body, signature):
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
            booking = await db.get(PaymentBookingProjection, payment.booking_id)
            if booking:
                await enqueue_event(
                    db,
                    topic="payment-events",
                    event_type="payment.captured",
                    event_key=str(payment.id),
                    payload={
                        "payment_id": str(payment.id),
                        "booking_id": str(booking.booking_id),
                        "booking_number": booking.booking_number,
                        "user_id": str(booking.user_id),
                        "pandit_id": str(booking.pandit_id),
                        "amount": float(payment.amount),
                    },
                )

    elif event == "payment.failed":
        payment.status = PaymentStatus.FAILED

        # Compensating transaction: release slot + cancel booking
        await enqueue_event(
            db,
            topic="payment-events",
            event_type="payment.failed",
            event_key=str(payment.id),
            payload={
                "payment_id": str(payment.id),
                "booking_id": str(payment.booking_id),
                "reason": "Payment failed",
            },
        )

    elif event == "refund.processed":
        refund_entity = payload.get("payload", {}).get("refund", {}).get("entity", {})
        payment.status = PaymentStatus.REFUNDED
        payment.refund_id = refund_entity.get("id")
        payment.refund_amount = float(refund_entity.get("amount", 0)) / 100
        payment.refunded_at = datetime.now(timezone.utc)
        booking = await db.get(PaymentBookingProjection, payment.booking_id)
        if booking:
            await enqueue_event(
                db,
                topic="payment-events",
                event_type="payment.refunded",
                event_key=str(payment.id),
                payload={
                    "payment_id": str(payment.id),
                    "booking_id": str(booking.booking_id),
                    "booking_number": booking.booking_number,
                    "user_id": str(booking.user_id),
                    "pandit_id": str(booking.pandit_id),
                    "amount": float(payment.refund_amount or 0),
                    "refund_id": payment.refund_id,
                    "reason": "Webhook refund processed",
                },
            )

    await db.commit()
    return {"status": "ok"}


# ── Refund ────────────────────────────────────────────────────

@router.post("/{payment_id}/refund", response_model=MessageResponse)
async def refund_payment(
    payment_id: UUID,
    amount: float = None,  # None = full refund
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Admin-triggered manual refund via Razorpay."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Required role: ['ADMIN']")

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
        booking = await db.get(PaymentBookingProjection, payment.booking_id)
        if booking:
            await enqueue_event(
                db,
                topic="payment-events",
                event_type="payment.refunded",
                event_key=str(payment.id),
                payload={
                    "payment_id": str(payment.id),
                    "booking_id": str(booking.booking_id),
                    "booking_number": booking.booking_number,
                    "user_id": str(booking.user_id),
                    "pandit_id": str(booking.pandit_id),
                    "amount": refund_amount_paise / 100,
                    "refund_id": payment.refund_id,
                    "reason": "Manual refund",
                },
            )
        await db.commit()

        return MessageResponse(message=f"Refund of ₹{refund_amount_paise/100} initiated successfully")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Refund failed: {str(e)}")


# ── Read Endpoints ────────────────────────────────────────────

@router.get("/me/history", response_model=list[PaymentResponse])
async def my_payment_history(
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Get authenticated user's payment history."""
    current_user_id = UUID(str(current_user.id))
    result = await db.execute(
        select(Payment)
        .where(Payment.user_id == current_user_id)
        .order_by(Payment.created_at.desc())
        .limit(50)
    )
    payments = result.scalars().all()
    return [PaymentResponse.model_validate(p) for p in payments]

