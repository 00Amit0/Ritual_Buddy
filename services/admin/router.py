"""
services/admin/router.py
Admin-only endpoints: pandit verification, user moderation,
platform analytics, and immutable audit log.

ALL mutations are logged to AdminAuditLog before returning.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from services.search.router import (
    _get_pandit_coordinates,
    delete_pandit,
    ensure_pandit_index,
    get_es_client,
    index_pandit,
)
from shared.middleware.auth import require_admin
from shared.models.models import (
    AdminAuditLog,
    Booking,
    BookingStatus,
    Notification,
    NotificationType,
    PanditProfile,
    Payment,
    PaymentStatus,
    Review,
    User,
    UserRole,
    VerificationStatus,
)
from shared.schemas.schemas import (
    AdminAnalyticsResponse,
    AdminRejectPanditRequest,
    AdminSuspendRequest,
    AdminVerifyPanditRequest,
    MessageResponse,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _log(
    db: AsyncSession,
    admin: User,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: dict | None = None,
    request: Request | None = None,
):
    """Append an immutable record to AdminAuditLog."""
    log = AdminAuditLog(
        admin_id=admin.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
        ip_address=request.client.host if request and request.client else None,
    )
    db.add(log)


async def _sync_pandit_search(db: AsyncSession, pandit: PanditProfile) -> None:
    user_result = await db.execute(select(User).where(User.id == pandit.user_id))
    pandit_user = user_result.scalar_one_or_none()
    es_client = await get_es_client()

    if not es_client or not pandit_user:
        return

    try:
        await ensure_pandit_index(es_client)
        latitude, longitude = await _get_pandit_coordinates(db, pandit)
        await index_pandit(
            es_client,
            pandit,
            pandit_user,
            latitude=latitude,
            longitude=longitude,
        )
    except Exception:
        pass
    finally:
        await es_client.close()


async def _remove_pandit_search(pandit_id: UUID) -> None:
    es_client = await get_es_client()
    if not es_client:
        return

    try:
        await delete_pandit(es_client, pandit_id)
    except Exception:
        pass
    finally:
        await es_client.close()


async def _notify_pandit(db: AsyncSession, pandit: PanditProfile, title: str, body: str) -> None:
    """Create an in-app notification for the pandit."""
    db.add(Notification(
        user_id=pandit.user_id,
        type=NotificationType.ACCOUNT_VERIFIED,
        title=title,
        body=body,
    ))


# ── Pandit Verification Queue ──────────────────────────────────────────────────

@router.get("/pandits/pending")
async def get_pending_pandits(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Pandits awaiting verification — ordered oldest first (FIFO queue).
    Returns profile info + uploaded document URLs for review.
    """
    query = (
        select(PanditProfile, User)
        .join(User, User.id == PanditProfile.user_id)
        .where(PanditProfile.verification_status == VerificationStatus.PENDING)
        .order_by(PanditProfile.created_at.asc())
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    rows = result.all()

    return {
        "items": [
            {
                "pandit_id": str(row[0].id),
                "user_id": str(row[0].user_id),
                "name": row[1].name,
                "email": row[1].email,
                "phone": row[1].phone,
                "city": row[0].city,
                "state": row[0].state,
                "experience_years": row[0].experience_years,
                "languages": row[0].languages,
                "poojas_offered": [str(p) for p in (row[0].poojas_offered or [])],
                "bio": row[0].bio,
                "documents": row[0].documents,
                "applied_at": row[0].created_at.isoformat(),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total // page_size),  # ceiling division
    }


@router.post("/pandits/{pandit_id}/verify", response_model=MessageResponse)
async def verify_pandit(
    pandit_id: UUID,
    data: AdminVerifyPanditRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """
    Approve a pandit's profile.
    - Sets status to VERIFIED
    - Makes them visible in search results
    - Sends in-app + push notification to the pandit
    - Syncs the search index
    """
    result = await db.execute(select(PanditProfile).where(PanditProfile.id == pandit_id))
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")
    if pandit.verification_status == VerificationStatus.VERIFIED:
        raise HTTPException(status_code=409, detail="Pandit is already verified")

    pandit.verification_status = VerificationStatus.VERIFIED
    pandit.verification_notes = data.notes
    pandit.verified_at = datetime.now(timezone.utc)
    pandit.verified_by_id = current_user.id
    await _notify_pandit(
        db,
        pandit,
        "Profile Verified! 🎉",
        "Congratulations! Your pandit profile has been verified. You can now accept bookings.",
    )

    await _log(db, current_user, "VERIFY_PANDIT", "PanditProfile", str(pandit_id),
               {"notes": data.notes}, request)

    await _sync_pandit_search(db, pandit)

    await db.commit()
    return MessageResponse(message="Pandit verified successfully")


@router.post("/pandits/{pandit_id}/reject", response_model=MessageResponse)
async def reject_pandit(
    pandit_id: UUID,
    data: AdminRejectPanditRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Reject a pandit application with a reason. Pandit can re-apply after fixing issues."""
    result = await db.execute(select(PanditProfile).where(PanditProfile.id == pandit_id))
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")

    pandit.verification_status = VerificationStatus.REJECTED
    pandit.verification_notes = data.reason
    await _notify_pandit(
        db,
        pandit,
        "Application Update",
        f"Your pandit profile application was not approved. Reason: {data.reason}",
    )

    await _log(db, current_user, "REJECT_PANDIT", "PanditProfile", str(pandit_id),
               {"reason": data.reason}, request)
    await db.commit()
    return MessageResponse(message="Pandit application rejected")


@router.post("/pandits/{pandit_id}/suspend", response_model=MessageResponse)
async def suspend_pandit(
    pandit_id: UUID,
    data: AdminSuspendRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Suspend a verified pandit. They cannot accept new bookings while suspended."""
    result = await db.execute(select(PanditProfile).where(PanditProfile.id == pandit_id))
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")

    pandit.verification_status = VerificationStatus.SUSPENDED
    pandit.is_available = False
    pandit.verification_notes = f"SUSPENDED: {data.reason}"
    await _notify_pandit(
        db,
        pandit,
        "Profile Suspended",
        f"Your pandit profile has been suspended. Reason: {data.reason}",
    )

    await _log(db, current_user, "SUSPEND_PANDIT", "PanditProfile", str(pandit_id),
               {"reason": data.reason, "duration_days": data.duration_days}, request)

    await _remove_pandit_search(pandit_id)
    await db.commit()
    return MessageResponse(message="Pandit suspended")


@router.post("/pandits/{pandit_id}/reinstate", response_model=MessageResponse)
async def reinstate_pandit(
    pandit_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Reinstate a previously suspended pandit."""
    result = await db.execute(select(PanditProfile).where(PanditProfile.id == pandit_id))
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")
    if pandit.verification_status != VerificationStatus.SUSPENDED:
        raise HTTPException(status_code=400, detail="Pandit is not suspended")

    pandit.verification_status = VerificationStatus.VERIFIED
    pandit.is_available = True
    pandit.verification_notes = None
    await _notify_pandit(
        db,
        pandit,
        "Profile Reinstated",
        "Your pandit profile has been reinstated and is visible for new bookings again.",
    )

    await _log(db, current_user, "REINSTATE_PANDIT", "PanditProfile", str(pandit_id), {}, request)
    await _sync_pandit_search(db, pandit)
    await db.commit()
    return MessageResponse(message="Pandit reinstated")


# ── User Moderation ────────────────────────────────────────────────────────────

@router.post("/users/{user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    user_id: UUID,
    data: AdminSuspendRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Deactivate a user account. Admins cannot be suspended."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Cannot suspend admin users")
    if not user.is_active:
        raise HTTPException(status_code=409, detail="User is already suspended")

    user.is_active = False
    if user.role == UserRole.PANDIT:
        pandit = await db.scalar(select(PanditProfile).where(PanditProfile.user_id == user.id))
        if pandit:
            pandit.is_available = False
    await _log(db, current_user, "SUSPEND_USER", "User", str(user_id),
               {"reason": data.reason}, request)
    await db.commit()
    return MessageResponse(message="User suspended")


@router.post("/users/{user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    user_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Re-activate a suspended user account."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_active:
        raise HTTPException(status_code=409, detail="User is already active")

    user.is_active = True
    if user.role == UserRole.PANDIT:
        pandit = await db.scalar(select(PanditProfile).where(PanditProfile.user_id == user.id))
        if pandit and pandit.verification_status == VerificationStatus.VERIFIED:
            pandit.is_available = True
    await _log(db, current_user, "REACTIVATE_USER", "User", str(user_id), {}, request)
    await db.commit()
    return MessageResponse(message="User reactivated")


# ── Booking Oversight ──────────────────────────────────────────────────────────

@router.get("/bookings")
async def list_all_bookings(
    status_filter: str = Query(None, description="Filter by BookingStatus enum value"),
    user_id: UUID = Query(None),
    pandit_id: UUID = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: view all bookings with status, user, or pandit filter."""
    query = select(Booking).order_by(Booking.created_at.desc())

    if status_filter:
        try:
            query = query.where(Booking.status == BookingStatus(status_filter))
        except ValueError:
            valid = [s.value for s in BookingStatus]
            raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {valid}")
    if user_id:
        query = query.where(Booking.user_id == user_id)
    if pandit_id:
        query = query.where(Booking.pandit_id == pandit_id)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    bookings = result.scalars().all()

    return {
        "items": [
            {
                "id": str(b.id),
                "booking_number": b.booking_number,
                "user_id": str(b.user_id),
                "pandit_id": str(b.pandit_id),
                "pooja_id": str(b.pooja_id),
                "status": b.status.value,
                "scheduled_at": b.scheduled_at.isoformat(),
                "total_amount": float(b.total_amount),
                "platform_fee": float(b.platform_fee),
                "pandit_payout": float(b.pandit_payout),
                "cancellation_reason": b.cancellation_reason,
                "created_at": b.created_at.isoformat(),
            }
            for b in bookings
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total // page_size),
    }


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def get_analytics(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide metrics dashboard. All queries run against the primary DB."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total_users = await db.scalar(
        select(func.count(User.id)).where(User.role == UserRole.USER)
    )
    total_pandits = await db.scalar(select(func.count(PanditProfile.id)))
    verified_pandits = await db.scalar(
        select(func.count(PanditProfile.id))
        .where(PanditProfile.verification_status == VerificationStatus.VERIFIED)
    )
    pending_verification = await db.scalar(
        select(func.count(PanditProfile.id))
        .where(PanditProfile.verification_status == VerificationStatus.PENDING)
    )
    total_bookings = await db.scalar(select(func.count(Booking.id)))
    bookings_today = await db.scalar(
        select(func.count(Booking.id)).where(Booking.created_at >= today_start)
    )
    total_revenue = await db.scalar(
        select(func.sum(Payment.amount)).where(Payment.status == PaymentStatus.CAPTURED)
    )
    revenue_today = await db.scalar(
        select(func.sum(Payment.amount)).where(
            Payment.status == PaymentStatus.CAPTURED,
            Payment.captured_at >= today_start,
        )
    )
    avg_rating = await db.scalar(
        select(func.avg(Review.rating)).where(Review.is_visible == True)
    )

    return AdminAnalyticsResponse(
        total_users=total_users or 0,
        total_pandits=total_pandits or 0,
        verified_pandits=verified_pandits or 0,
        pending_verification=pending_verification or 0,
        total_bookings=total_bookings or 0,
        bookings_today=bookings_today or 0,
        total_revenue=Decimal(str(total_revenue or 0)),
        revenue_today=Decimal(str(revenue_today or 0)),
        avg_rating=float(avg_rating or 0),
    )


# ── Audit Log ─────────────────────────────────────────────────────────────────

@router.get("/audit-logs")
async def get_audit_logs(
    action: str = Query(None, description="Filter by action type e.g. VERIFY_PANDIT"),
    entity_type: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Immutable admin audit log — append-only, never editable."""
    query = (
        select(AdminAuditLog, User)
        .join(User, User.id == AdminAuditLog.admin_id)
        .order_by(AdminAuditLog.created_at.desc())
    )
    if action:
        query = query.where(AdminAuditLog.action == action.upper())
    if entity_type:
        query = query.where(AdminAuditLog.entity_type == entity_type)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    rows = result.all()

    return {
        "items": [
            {
                "id": str(row[0].id),
                "admin_name": row[1].name,
                "admin_email": row[1].email,
                "action": row[0].action,
                "entity_type": row[0].entity_type,
                "entity_id": row[0].entity_id,
                "payload": row[0].payload,
                "ip_address": row[0].ip_address,
                "created_at": row[0].created_at.isoformat(),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
