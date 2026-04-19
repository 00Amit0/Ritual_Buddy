"""
services/booking/router.py
Booking lifecycle management using the Saga orchestration pattern.
States: DRAFT → SLOT_LOCKED → PAYMENT_PENDING → AWAITING_PANDIT
        → CONFIRMED | DECLINED → COMPLETED | CANCELLED
"""

import random
import string
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.redis_client import RedisCache, get_redis
from config.settings import settings
from shared.middleware.auth import get_current_user, require_pandit
from shared.models.models import (
    Booking,
    BookingAuditLog,
    BookingStatus,
    PanditAvailability,
    PanditProfile,
    Payment,
    PaymentStatus,
    Pooja,
    User,
    UserRole,
    VerificationStatus,
)
from shared.schemas.schemas import (
    BookingCancelRequest,
    BookingCreateRequest,
    BookingDeclineRequest,
    BookingResponse,
    MessageResponse,
)

router = APIRouter(prefix="/bookings", tags=["Bookings"])


# ── Helpers ───────────────────────────────────────────────────

def _generate_booking_number() -> str:
    """Generate a human-readable booking number like PB-2024-X7K9M."""
    year = datetime.now().year
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"PB-{year}-{suffix}"


async def _get_booking_or_404(booking_id: UUID, db: AsyncSession) -> Booking:
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


async def _log_status_change(
    db: AsyncSession,
    booking: Booking,
    from_status: str,
    to_status: str,
    changed_by: User,
    reason: str = None,
    metadata: dict = None,
):
    """Append an immutable audit log entry for every status change."""
    log = BookingAuditLog(
        booking_id=booking.id,
        from_status=from_status,
        to_status=to_status,
        changed_by_id=changed_by.id,
        reason=reason,
        audit_metadata=metadata,
    )
    db.add(log)


def _enrich_booking(booking: Booking) -> BookingResponse:
    return BookingResponse(
        **{
            col.name: getattr(booking, col.name)
            for col in Booking.__table__.columns
        }
    )


def _time_in_slot(target: dt_time, start_time: str, end_time: str) -> bool:
    """Return True when the target time falls within the availability slot."""
    start = datetime.strptime(start_time, "%H:%M:%S").time()
    end = datetime.strptime(end_time, "%H:%M:%S").time()
    return start <= target < end


async def _find_matching_slot(
    db: AsyncSession,
    pandit_id: UUID,
    scheduled_at: datetime,
    *,
    only_unbooked: bool = True,
) -> PanditAvailability | None:
    """Find the availability slot that actually covers the booking datetime."""
    slot_result = await db.execute(
        select(PanditAvailability).where(
            PanditAvailability.pandit_id == pandit_id,
            func.date(PanditAvailability.date) == scheduled_at.date(),
            PanditAvailability.is_blocked == False,
        )
    )
    slots = slot_result.scalars().all()
    target_time = scheduled_at.timetz().replace(tzinfo=None)

    for slot in slots:
        if only_unbooked and slot.is_booked:
            continue
        if _time_in_slot(target_time, slot.start_time, slot.end_time):
            return slot

    return None


async def _send_notification_async(
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    booking_id: str = None,
    db: AsyncSession = None,
):
    """
    Fire-and-forget notification. In production, publish to Kafka/BullMQ.
    For now, we store in DB directly.
    """
    from shared.models.models import Notification, NotificationType
    try:
        notif = Notification(
            user_id=user_id,
            booking_id=booking_id,
            type=NotificationType[notification_type],
            title=title,
            body=body,
        )
        db.add(notif)
        # In production: also push to FCM, SMS etc. via notification service
    except Exception:
        pass  # Don't fail booking flow on notification errors


# ── Booking Creation (Saga Step 1) ────────────────────────────

@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    data: BookingCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Initiate a booking. Steps:
    1. Validate pandit + pooja exist and pandit is verified
    2. Check pandit availability
    3. Soft-lock the slot in Redis (15min TTL)
    4. Create SLOT_LOCKED booking record
    5. Return booking → client initiates payment next
    """
    if current_user.role == UserRole.PANDIT:
        raise HTTPException(status_code=403, detail="Pandits cannot book other pandits")

    # Step 1: Validate pandit
    result = await db.execute(select(PanditProfile).where(PanditProfile.id == data.pandit_id))
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")
    if pandit.verification_status != VerificationStatus.VERIFIED:
        raise HTTPException(status_code=400, detail="Pandit is not verified")
    if not pandit.is_available:
        raise HTTPException(status_code=400, detail="Pandit is currently not accepting bookings")

    # Step 2: Validate pooja
    result = await db.execute(select(Pooja).where(Pooja.id == data.pooja_id))
    pooja = result.scalar_one_or_none()
    if not pooja:
        raise HTTPException(status_code=404, detail="Pooja type not found")

    # Step 3: Check availability slot
    scheduled_date = data.scheduled_at.date()
    available_slot = await _find_matching_slot(
        db,
        pandit.id,
        data.scheduled_at,
        only_unbooked=True,
    )
    if not available_slot:
        raise HTTPException(
            status_code=400,
            detail="No available slot for the selected time. Please choose another slot.",
        )

    # Step 4: Soft-lock slot in Redis
    slot_key = f"{pandit.id}:{data.scheduled_at.isoformat()}"
    cache = RedisCache(redis)

    existing_lock = await cache.get_slot_lock(str(pandit.id), data.scheduled_at.isoformat())
    if existing_lock:
        raise HTTPException(
            status_code=409,
            detail="This slot is temporarily held by another booking. Please try again in a few minutes.",
        )

    # Step 5: Calculate amounts
    pooja_fee = float(pandit.pooja_fees or {}).get(str(data.pooja_id), float(pandit.base_fee))
    platform_fee = round(pooja_fee * settings.PLATFORM_COMMISSION_PERCENT / 100, 2)
    total_amount = round(pooja_fee + platform_fee, 2)
    pandit_payout = round(pooja_fee - platform_fee, 2)

    # Step 6: Create booking
    booking = Booking(
        booking_number=_generate_booking_number(),
        user_id=current_user.id,
        pandit_id=pandit.id,
        pooja_id=pooja.id,
        scheduled_at=data.scheduled_at,
        duration_hrs=pooja.avg_duration_hrs,
        status=BookingStatus.SLOT_LOCKED,
        address=data.address.model_dump(),
        special_requirements=data.special_requirements,
        base_amount=pooja_fee,
        platform_fee=platform_fee,
        total_amount=total_amount,
        pandit_payout=pandit_payout,
        accept_deadline=data.scheduled_at - timedelta(hours=settings.BOOKING_ACCEPT_DEADLINE_HOURS),
    )
    db.add(booking)
    await db.flush()

    # Lock slot in Redis
    await cache.lock_slot(str(pandit.id), data.scheduled_at.isoformat(), str(booking.id))

    # Audit log
    await _log_status_change(
        db,
        booking,
        None,
        BookingStatus.SLOT_LOCKED.value,
        current_user,
        metadata={"slot_id": str(available_slot.id)},
    )
    await db.commit()

    return _enrich_booking(booking)


# ── Payment Confirmation (Saga Step 2 - called by Payment Service) ──

@router.post("/{booking_id}/payment-confirmed", include_in_schema=False)
async def payment_confirmed(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Internal endpoint called by Payment Service after successful payment.
    Transitions booking: SLOT_LOCKED → AWAITING_PANDIT.
    Notifies pandit to accept/decline.
    """
    booking = await _get_booking_or_404(booking_id, db)

    if booking.status not in (BookingStatus.SLOT_LOCKED, BookingStatus.PAYMENT_PENDING):
        raise HTTPException(status_code=400, detail=f"Cannot confirm payment for booking in {booking.status} state")

    prev_status = booking.status.value
    booking.status = BookingStatus.AWAITING_PANDIT

    # Audit log
    from shared.models.models import Notification, NotificationType
    pandit_result = await db.execute(select(PanditProfile).where(PanditProfile.id == booking.pandit_id))
    pandit = pandit_result.scalar_one_or_none()

    if pandit:
        # Notify pandit
        notif = Notification(
            user_id=pandit.user_id,
            booking_id=booking.id,
            type=NotificationType.BOOKING_CREATED,
            title="New Booking Request 🙏",
            body=f"You have a new booking request for {booking.scheduled_at.strftime('%d %b %Y')}. Please accept or decline within {settings.BOOKING_ACCEPT_DEADLINE_HOURS} hours.",
        )
        db.add(notif)

    await db.commit()
    return {"status": "ok"}


# ── Pandit Accept/Decline ─────────────────────────────────────

@router.post("/{booking_id}/accept", response_model=BookingResponse)
async def accept_booking(
    booking_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Pandit accepts the booking. Status: AWAITING_PANDIT → CONFIRMED."""
    booking = await _get_booking_or_404(booking_id, db)

    # Verify this pandit owns this booking
    pandit_result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = pandit_result.scalar_one_or_none()
    if not pandit or booking.pandit_id != pandit.id:
        raise HTTPException(status_code=403, detail="Not authorized to accept this booking")

    if booking.status != BookingStatus.AWAITING_PANDIT:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot accept booking in '{booking.status.value}' state",
        )

    # Check deadline
    if booking.accept_deadline and datetime.now(timezone.utc) > booking.accept_deadline:
        raise HTTPException(status_code=400, detail="Acceptance deadline has passed")

    prev_status = booking.status.value
    booking.status = BookingStatus.CONFIRMED
    booking.confirmed_at = datetime.now(timezone.utc)

    # Mark the exact matched slot as booked
    slot = await _find_matching_slot(
        db,
        pandit.id,
        booking.scheduled_at,
        only_unbooked=True,
    )
    if slot:
        slot.is_booked = True
        slot.booking_id = booking.id
    else:
        raise HTTPException(
            status_code=409,
            detail="The booked availability slot could not be found anymore",
        )

    # Release Redis lock (permanent DB booking replaces it)
    cache = RedisCache(redis)
    await cache.release_slot(str(pandit.id), booking.scheduled_at.isoformat())

    await _log_status_change(db, booking, prev_status, BookingStatus.CONFIRMED.value, current_user)

    # Notify user
    await _send_notification_async(
        user_id=str(booking.user_id),
        notification_type="BOOKING_CONFIRMED",
        title="Booking Confirmed! 🎉",
        body=f"Your booking #{booking.booking_number} has been confirmed by the Pandit.",
        booking_id=str(booking.id),
        db=db,
    )

    await db.commit()
    return _enrich_booking(booking)


@router.post("/{booking_id}/decline", response_model=BookingResponse)
async def decline_booking(
    booking_id: UUID,
    data: BookingDeclineRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Pandit declines the booking.
    Compensating transaction: release slot + trigger refund.
    """
    booking = await _get_booking_or_404(booking_id, db)

    pandit_result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = pandit_result.scalar_one_or_none()
    if not pandit or booking.pandit_id != pandit.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if booking.status != BookingStatus.AWAITING_PANDIT:
        raise HTTPException(status_code=400, detail=f"Cannot decline booking in '{booking.status.value}' state")

    prev_status = booking.status.value
    booking.status = BookingStatus.DECLINED
    booking.decline_reason = data.reason
    booking.cancelled_at = datetime.now(timezone.utc)

    # Release slot lock
    cache = RedisCache(redis)
    await cache.release_slot(str(pandit.id), booking.scheduled_at.isoformat())

    await _log_status_change(
        db, booking, prev_status, BookingStatus.DECLINED.value, current_user, data.reason
    )

    payment_result = await db.execute(
        select(Payment).where(Payment.booking_id == booking.id)
    )
    payment = payment_result.scalar_one_or_none()

    # Notify user
    await _send_notification_async(
        user_id=str(booking.user_id),
        notification_type="BOOKING_DECLINED",
        title="Booking Declined",
        body=f"The Pandit has declined booking #{booking.booking_number}. A full refund will be processed within 3-5 business days.",
        booking_id=str(booking.id),
        db=db,
    )

    await db.commit()

    if payment and payment.status == PaymentStatus.CAPTURED:
        from tasks.payment_tasks import process_refund

        process_refund.apply_async(
            args=[str(payment.id), float(payment.amount), "Pandit declined booking"],
            queue="payments",
        )

    return _enrich_booking(booking)


@router.post("/{booking_id}/complete", response_model=BookingResponse)
async def complete_booking(
    booking_id: UUID,
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
):
    """Pandit marks booking as completed. Triggers payout + review request."""
    booking = await _get_booking_or_404(booking_id, db)

    pandit_result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = pandit_result.scalar_one_or_none()
    if not pandit or booking.pandit_id != pandit.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if booking.status != BookingStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="Booking must be CONFIRMED to complete")

    prev_status = booking.status.value
    booking.status = BookingStatus.COMPLETED
    booking.completed_at = datetime.now(timezone.utc)

    await _log_status_change(db, booking, prev_status, BookingStatus.COMPLETED.value, current_user)

    payment_result = await db.execute(
        select(Payment).where(
            Payment.booking_id == booking.id,
            Payment.status == PaymentStatus.CAPTURED,
        )
    )
    payment = payment_result.scalar_one_or_none()

    await db.commit()

    if payment and not payment.payout_id:
        from tasks.payment_tasks import process_single_payout

        process_single_payout.apply_async(
            args=[str(payment.id)],
            queue="payments",
        )

    return _enrich_booking(booking)


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: UUID,
    data: BookingCancelRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    User or admin cancels a booking.
    Cancellation policy: full refund if >24hr before, 50% if <24hr.
    """
    booking = await _get_booking_or_404(booking_id, db)

    # Authorization: user can cancel own booking, pandit can cancel assigned booking, admin can cancel any
    if current_user.role == UserRole.USER:
        if booking.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized")
    elif current_user.role == UserRole.PANDIT:
        pandit = await db.scalar(
            select(PanditProfile).where(PanditProfile.user_id == current_user.id)
        )
        if not pandit or booking.pandit_id != pandit.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    cancellable_statuses = [
        BookingStatus.SLOT_LOCKED,
        BookingStatus.PAYMENT_PENDING,
        BookingStatus.AWAITING_PANDIT,
        BookingStatus.CONFIRMED,
    ]
    if booking.status not in cancellable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Booking in '{booking.status.value}' state cannot be cancelled",
        )

    prev_status = booking.status.value
    booking.status = BookingStatus.CANCELLED
    booking.cancellation_reason = data.reason
    booking.cancelled_by = current_user.role.value
    booking.cancelled_at = datetime.now(timezone.utc)

    # Release slot lock if still held
    pandit_result = await db.execute(
        select(PanditProfile).where(PanditProfile.id == booking.pandit_id)
    )
    pandit = pandit_result.scalar_one_or_none()
    if pandit:
        cache = RedisCache(redis)
        await cache.release_slot(str(pandit.id), booking.scheduled_at.isoformat())

        # Un-book the slot if it was marked booked
        slot_result = await db.execute(
            select(PanditAvailability).where(PanditAvailability.booking_id == booking.id)
        )
        slot = slot_result.scalar_one_or_none()
        if slot:
            slot.is_booked = False
            slot.booking_id = None

    await _log_status_change(
        db, booking, prev_status, BookingStatus.CANCELLED.value, current_user, data.reason
    )

    payment_result = await db.execute(
        select(Payment).where(Payment.booking_id == booking.id)
    )
    payment = payment_result.scalar_one_or_none()

    await db.commit()

    if payment and payment.status == PaymentStatus.CAPTURED:
        from tasks.payment_tasks import process_refund

        refund_ratio = 1.0 if booking.scheduled_at - datetime.now(timezone.utc) > timedelta(hours=24) else 0.5
        refund_amount = round(float(payment.amount) * refund_ratio, 2)
        reason = "User cancellation with full refund" if refund_ratio == 1.0 else "User cancellation with 50% refund"

        process_refund.apply_async(
            args=[str(payment.id), refund_amount, reason],
            queue="payments",
        )

    return _enrich_booking(booking)


# ── Read Endpoints ────────────────────────────────────────────

@router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(
    booking_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get booking details. User sees own, pandit sees their bookings, admin sees all."""
    booking = await _get_booking_or_404(booking_id, db)

    # Authorization check
    if current_user.role == UserRole.USER and booking.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    elif current_user.role == UserRole.PANDIT:
        pandit_result = await db.execute(
            select(PanditProfile).where(PanditProfile.user_id == current_user.id)
        )
        pandit = pandit_result.scalar_one_or_none()
        if not pandit or booking.pandit_id != pandit.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    return _enrich_booking(booking)


@router.get("", response_model=list[BookingResponse])
async def list_my_bookings(
    status_filter: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List bookings for the current user. Pandits see bookings assigned to them."""
    if current_user.role == UserRole.PANDIT:
        pandit_result = await db.execute(
            select(PanditProfile).where(PanditProfile.user_id == current_user.id)
        )
        pandit = pandit_result.scalar_one_or_none()
        if not pandit:
            return []
        query = select(Booking).where(Booking.pandit_id == pandit.id)
    else:
        query = select(Booking).where(Booking.user_id == current_user.id)

    if status_filter:
        try:
            query = query.where(Booking.status == BookingStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status_filter}")

    query = query.order_by(Booking.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    bookings = result.scalars().all()
    return [_enrich_booking(b) for b in bookings]
