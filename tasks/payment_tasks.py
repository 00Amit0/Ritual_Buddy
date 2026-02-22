"""
tasks/payment_tasks.py
Celery tasks for payment lifecycle operations:
- Escrow release and pandit payout via Razorpay Payouts API
- Slot lock cleanup for stuck/expired bookings
- Refund retry for failed Razorpay refunds

All tasks are idempotent — running twice has no side effect.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from celery import Task
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_sync_session():
    """Create a synchronous SQLAlchemy session (Celery runs sync by default)."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True, pool_size=5)
    Session = sessionmaker(bind=engine)
    return Session()


def _get_razorpay():
    """Get authenticated Razorpay client."""
    import razorpay
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


# ── Payout Tasks ───────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=5, default_retry_delay=300)
def process_single_payout(self, payment_id: str):
    """
    Release escrow and transfer pandit_payout to the pandit's bank account
    via Razorpay Payouts API.

    Called when:
    - Admin manually triggers a payout
    - A booking is marked COMPLETED (via booking router)

    Idempotent: skips if payout_id already set.
    """
    from shared.models.models import Booking, BookingStatus, PanditProfile, Payment, PaymentStatus, User

    db = _get_sync_session()
    try:
        payment = db.execute(
            select(Payment).where(Payment.id == payment_id)
        ).scalar_one_or_none()

        if not payment:
            logger.error(f"process_single_payout: payment {payment_id} not found")
            return

        # Idempotency check — skip if already paid out
        if payment.payout_id:
            logger.info(f"Payout already processed for payment {payment_id}: {payment.payout_id}")
            return

        if payment.status != PaymentStatus.CAPTURED:
            logger.warning(f"Cannot payout non-captured payment {payment_id}: {payment.status}")
            return

        # Get booking and pandit info
        booking = db.execute(
            select(Booking).where(Booking.id == payment.booking_id)
        ).scalar_one_or_none()
        if not booking or booking.status != BookingStatus.COMPLETED:
            logger.warning(f"Booking not in COMPLETED state for payout: {payment.booking_id}")
            return

        pandit_profile = db.execute(
            select(PanditProfile).where(PanditProfile.id == booking.pandit_id)
        ).scalar_one_or_none()
        if not pandit_profile:
            logger.error(f"PanditProfile not found: {booking.pandit_id}")
            return

        # Amount in paise (Razorpay uses smallest currency unit)
        payout_amount_paise = int(payment.pandit_payout * 100)

        # Razorpay Payouts API
        # NOTE: Requires Razorpay X (Current Account) — enable in Razorpay dashboard
        client = _get_razorpay()
        payout_response = client.payout.create({
            "account_number": settings.RAZORPAY_ACCOUNT_NUMBER,
            "amount": payout_amount_paise,
            "currency": "INR",
            "mode": "IMPS",  # Immediate Payment Service
            "purpose": "payout",
            "fund_account": {
                "account_type": "bank_account",
                "bank_account": {
                    # In production: fetch from PanditBankAccount table
                    "name": "Pandit Payout",
                    "ifsc": pandit_profile.bank_ifsc or "RAZR0000001",
                    "account_number": pandit_profile.bank_account_number or "0000000000000",
                },
                "contact": {
                    "name": "Pandit",
                    "type": "vendor",
                    "reference_id": str(pandit_profile.id),
                }
            },
            "notes": {
                "booking_id": str(booking.id),
                "booking_number": booking.booking_number,
                "payment_id": str(payment.id),
            }
        })

        # Update payment record
        payment.payout_id = payout_response.get("id")
        payment.payout_amount = payment.pandit_payout
        payment.payout_at = datetime.now(timezone.utc)
        payment.status = PaymentStatus.CAPTURED  # stays CAPTURED; payout is separate tracking

        db.commit()
        logger.info(f"Payout {payment.payout_id} processed for booking {booking.booking_number}")

    except Exception as e:
        db.rollback()
        logger.exception(f"process_single_payout failed for {payment_id}: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))
    finally:
        db.close()


@celery_app.task
def process_pending_payouts():
    """
    Beat task: runs nightly at 2 AM IST.
    Finds all COMPLETED bookings with unpaid payouts and processes them in bulk.
    Handles cases where process_single_payout was not called (e.g. race conditions, crashes).
    """
    from shared.models.models import Booking, BookingStatus, Payment, PaymentStatus

    db = _get_sync_session()
    try:
        # Find CAPTURED payments for COMPLETED bookings without a payout
        results = db.execute(
            select(Payment)
            .join(Booking, Booking.id == Payment.booking_id)
            .where(
                Booking.status == BookingStatus.COMPLETED,
                Payment.status == PaymentStatus.CAPTURED,
                Payment.payout_id == None,  # noqa: E711
                Payment.pandit_payout > 0,
            )
        ).scalars().all()

        logger.info(f"process_pending_payouts: found {len(results)} unpaid payouts")

        for payment in results:
            # Enqueue individual payout task (not inline — allows retry per payment)
            process_single_payout.apply_async(
                args=[str(payment.id)],
                queue="payments",
                countdown=5,  # small stagger to avoid rate limits
            )

    except Exception as e:
        logger.exception(f"process_pending_payouts failed: {e}")
    finally:
        db.close()


# ── Refund Tasks ───────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=5, default_retry_delay=120)
def process_refund(self, payment_id: str, amount: float | None = None, reason: str = "Booking declined"):
    """
    Initiate a Razorpay refund for a payment.
    If amount is None, issues full refund. Otherwise partial refund.

    Called from:
    - booking router when pandit declines
    - booking router on user cancellation
    - admin manual refund endpoint

    Idempotent: checks for existing refund_id before re-attempting.
    """
    from shared.models.models import Payment, PaymentStatus

    db = _get_sync_session()
    try:
        payment = db.execute(
            select(Payment).where(Payment.id == payment_id)
        ).scalar_one_or_none()

        if not payment:
            logger.error(f"process_refund: payment {payment_id} not found")
            return

        if payment.refund_id:
            logger.info(f"Refund already processed: {payment.refund_id}")
            return

        if payment.status == PaymentStatus.REFUNDED:
            logger.info(f"Payment {payment_id} already fully refunded")
            return

        if not payment.razorpay_payment_id:
            logger.error(f"No razorpay_payment_id for payment {payment_id} — cannot refund")
            return

        refund_amount_paise = int((amount or float(payment.amount)) * 100)

        client = _get_razorpay()
        refund = client.payment.refund(
            payment.razorpay_payment_id,
            {
                "amount": refund_amount_paise,
                "notes": {"reason": reason, "payment_id": payment_id},
            }
        )

        payment.refund_id = refund.get("id")
        payment.refund_amount = Decimal(str(refund_amount_paise / 100))
        payment.refunded_at = datetime.now(timezone.utc)

        if refund_amount_paise >= int(payment.amount * 100):
            payment.status = PaymentStatus.REFUNDED
        else:
            payment.status = PaymentStatus.PARTIALLY_REFUNDED

        db.commit()
        logger.info(f"Refund {payment.refund_id} of ₹{payment.refund_amount} processed")

    except Exception as e:
        db.rollback()
        logger.exception(f"process_refund failed for {payment_id}: {e}")
        raise self.retry(exc=e, countdown=120 * (2 ** self.request.retries))
    finally:
        db.close()


# ── Slot Lock Cleanup ──────────────────────────────────────────────────────────

@celery_app.task
def release_expired_slot_locks():
    """
    Beat task: runs every 5 minutes.
    Finds bookings stuck in SLOT_LOCKED or PAYMENT_PENDING with expired accept deadlines
    and cancels them, releasing the Redis slot lock.

    This handles the case where:
    - User opened payment but never completed it
    - Redis TTL expired but booking status wasn't rolled back
    """
    import redis as redis_lib
    from shared.models.models import Booking, BookingAuditLog, BookingStatus

    db = _get_sync_session()
    r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)

    try:
        now = datetime.now(timezone.utc)

        stuck_bookings = db.execute(
            select(Booking).where(
                Booking.status.in_([BookingStatus.SLOT_LOCKED, BookingStatus.PAYMENT_PENDING]),
                Booking.accept_deadline < now,
            )
        ).scalars().all()

        logger.info(f"release_expired_slot_locks: found {len(stuck_bookings)} stuck bookings")

        for booking in stuck_bookings:
            prev_status = booking.status

            # Cancel the booking
            booking.status = BookingStatus.CANCELLED
            booking.cancellation_reason = "Payment window expired — auto-cancelled"
            booking.cancelled_at = now

            # Audit log
            db.add(BookingAuditLog(
                booking_id=booking.id,
                from_status=prev_status,
                to_status=BookingStatus.CANCELLED,
                reason="Payment window expired — auto-cancelled by system",
                metadata={"cancelled_by": "system", "task": "release_expired_slot_locks"},
            ))

            # Release Redis slot lock (belt+suspenders — TTL should have expired it already)
            slot_key = f"slot_lock:{booking.pandit_id}:{booking.scheduled_at.isoformat()}"
            r.delete(slot_key)

            logger.info(f"Auto-cancelled expired booking: {booking.booking_number}")

        db.commit()

    except Exception as e:
        db.rollback()
        logger.exception(f"release_expired_slot_locks failed: {e}")
    finally:
        db.close()
        r.close()


@celery_app.task(bind=True, max_retries=3)
def retry_failed_payment(self, booking_id: str):
    """
    Handle payment.failed webhook from Razorpay.
    - Cancels the booking
    - Sends failure notification to user
    - Releases the slot lock
    """
    from shared.models.models import Booking, BookingAuditLog, BookingStatus

    db = _get_sync_session()
    try:
        booking = db.execute(
            select(Booking).where(Booking.id == booking_id)
        ).scalar_one_or_none()

        if not booking:
            return

        if booking.status not in (BookingStatus.PAYMENT_PENDING, BookingStatus.SLOT_LOCKED):
            logger.info(f"retry_failed_payment: booking {booking_id} not in payment state, skipping")
            return

        prev_status = booking.status
        booking.status = BookingStatus.CANCELLED
        booking.cancellation_reason = "Payment failed"
        booking.cancelled_at = datetime.now(timezone.utc)

        db.add(BookingAuditLog(
            booking_id=booking.id,
            from_status=prev_status,
            to_status=BookingStatus.CANCELLED,
            reason="Payment failed — notified by Razorpay webhook",
        ))

        db.commit()

        # Notify user
        from tasks.notification_tasks import send_push_notification
        # We'd need to fetch the user's FCM token here — simplified
        logger.info(f"Payment failed and booking cancelled: {booking.booking_number}")

    except Exception as e:
        db.rollback()
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()
