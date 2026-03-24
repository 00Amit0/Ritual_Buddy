"""
services/admin/router.py
Admin-only endpoints: pandit verification, user moderation,
platform analytics, and immutable audit log.

All mutations are logged to AdminAuditLog before returning.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from shared.events.outbox import enqueue_event
from shared.middleware.auth import require_admin_principal
from shared.models.models import (
    AdminAuditLog,
    AdminPanditReviewProjection,
    AdminBookingProjection,
    AdminPaymentProjection,
    AdminReviewProjection,
    AdminUserProjection,
    BookingStatus,
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


async def _log(
    db: AsyncSession,
    admin,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: dict | None = None,
    request: Request | None = None,
):
    log = AdminAuditLog(
        admin_id=UUID(str(admin.id)),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
        ip_address=request.client.host if request and request.client else None,
    )
    db.add(log)


async def _get_pandit_review_projection_or_404(
    pandit_id: UUID,
    db: AsyncSession,
) -> AdminPanditReviewProjection:
    projection = await db.get(AdminPanditReviewProjection, pandit_id)
    if not projection:
        raise HTTPException(status_code=404, detail="Pandit not found")
    return projection


async def _get_admin_user_projection_or_404(
    user_id: UUID,
    db: AsyncSession,
) -> AdminUserProjection:
    projection = await db.get(AdminUserProjection, user_id)
    if not projection:
        raise HTTPException(status_code=404, detail="User not found")
    return projection


@router.get("/pandits/pending")
async def get_pending_pandits(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(AdminPanditReviewProjection)
        .where(AdminPanditReviewProjection.verification_status == VerificationStatus.PENDING.value)
        .order_by(AdminPanditReviewProjection.created_at.asc())
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    rows = result.scalars().all()

    return {
        "items": [
            {
                "pandit_id": str(row.pandit_id),
                "user_id": str(row.user_id),
                "name": row.name,
                "email": row.email,
                "phone": row.phone,
                "city": row.city,
                "state": row.state,
                "experience_years": row.experience_years,
                "languages": row.languages,
                "poojas_offered": [str(p) for p in (row.poojas_offered or [])],
                "bio": row.bio,
                "documents": row.documents,
                "applied_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total // page_size),
    }


@router.post("/pandits/{pandit_id}/verify", response_model=MessageResponse)
async def verify_pandit(
    pandit_id: UUID,
    data: AdminVerifyPanditRequest,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    projection = await _get_pandit_review_projection_or_404(pandit_id, db)
    if projection.verification_status == VerificationStatus.VERIFIED.value:
        raise HTTPException(status_code=409, detail="Pandit is already verified")

    await _log(db, current_user, "VERIFY_PANDIT", "PanditProfile", str(pandit_id), {"notes": data.notes}, request)
    await enqueue_event(
        db,
        topic="admin-commands",
        event_type="admin.verify_pandit_requested",
        event_key=str(pandit_id),
        payload={
            "pandit_id": str(pandit_id),
            "verified_by": str(current_user.id),
            "notes": data.notes,
        },
    )

    await db.commit()
    return MessageResponse(message="Pandit verification queued")


@router.post("/pandits/{pandit_id}/reject", response_model=MessageResponse)
async def reject_pandit(
    pandit_id: UUID,
    data: AdminRejectPanditRequest,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    projection = await _get_pandit_review_projection_or_404(pandit_id, db)
    if projection.verification_status == VerificationStatus.REJECTED.value:
        raise HTTPException(status_code=409, detail="Pandit is already rejected")

    await _log(db, current_user, "REJECT_PANDIT", "PanditProfile", str(pandit_id), {"reason": data.reason}, request)
    await enqueue_event(
        db,
        topic="admin-commands",
        event_type="admin.reject_pandit_requested",
        event_key=str(pandit_id),
        payload={
            "pandit_id": str(pandit_id),
            "reason": data.reason,
            "rejected_by": str(current_user.id),
        },
    )

    await db.commit()
    return MessageResponse(message="Pandit rejection queued")


@router.post("/pandits/{pandit_id}/suspend", response_model=MessageResponse)
async def suspend_pandit(
    pandit_id: UUID,
    data: AdminSuspendRequest,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    projection = await _get_pandit_review_projection_or_404(pandit_id, db)
    if projection.verification_status == VerificationStatus.SUSPENDED.value:
        raise HTTPException(status_code=409, detail="Pandit is already suspended")

    await _log(
        db,
        current_user,
        "SUSPEND_PANDIT",
        "PanditProfile",
        str(pandit_id),
        {"reason": data.reason, "duration_days": data.duration_days},
        request,
    )
    await enqueue_event(
        db,
        topic="admin-commands",
        event_type="admin.suspend_pandit_requested",
        event_key=str(pandit_id),
        payload={
            "pandit_id": str(pandit_id),
            "reason": data.reason,
            "duration_days": data.duration_days,
            "suspended_by": str(current_user.id),
        },
    )

    await db.commit()
    return MessageResponse(message="Pandit suspension queued")


@router.post("/pandits/{pandit_id}/reinstate", response_model=MessageResponse)
async def reinstate_pandit(
    pandit_id: UUID,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    projection = await _get_pandit_review_projection_or_404(pandit_id, db)
    if projection.verification_status != VerificationStatus.SUSPENDED.value:
        raise HTTPException(status_code=400, detail="Pandit is not suspended")

    await _log(db, current_user, "REINSTATE_PANDIT", "PanditProfile", str(pandit_id), {}, request)
    await enqueue_event(
        db,
        topic="admin-commands",
        event_type="admin.reinstate_pandit_requested",
        event_key=str(pandit_id),
        payload={
            "pandit_id": str(pandit_id),
            "reinstated_by": str(current_user.id),
        },
    )

    await db.commit()
    return MessageResponse(message="Pandit reinstatement queued")


@router.post("/users/{user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    user_id: UUID,
    data: AdminSuspendRequest,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    projection = await _get_admin_user_projection_or_404(user_id, db)
    if projection.role == UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Cannot suspend admin users")
    if not projection.is_active:
        raise HTTPException(status_code=409, detail="User is already suspended")

    await _log(db, current_user, "SUSPEND_USER", "User", str(user_id), {"reason": data.reason}, request)
    await enqueue_event(
        db,
        topic="admin-commands",
        event_type="admin.suspend_user_requested",
        event_key=str(user_id),
        payload={
            "user_id": str(user_id),
            "reason": data.reason,
            "suspended_by": str(current_user.id),
        },
    )
    await db.commit()
    return MessageResponse(message="User suspension queued")


@router.post("/users/{user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    user_id: UUID,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    projection = await _get_admin_user_projection_or_404(user_id, db)
    if projection.is_active:
        raise HTTPException(status_code=409, detail="User is already active")

    await _log(db, current_user, "REACTIVATE_USER", "User", str(user_id), {}, request)
    await enqueue_event(
        db,
        topic="admin-commands",
        event_type="admin.reactivate_user_requested",
        event_key=str(user_id),
        payload={
            "user_id": str(user_id),
            "reactivated_by": str(current_user.id),
        },
    )
    await db.commit()
    return MessageResponse(message="User reactivation queued")


@router.get("/bookings")
async def list_all_bookings(
    status_filter: str = Query(None, description="Filter by BookingStatus enum value"),
    user_id: UUID = Query(None),
    pandit_id: UUID = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
):
    query = select(AdminBookingProjection).order_by(AdminBookingProjection.created_at.desc())

    if status_filter:
        try:
            query = query.where(AdminBookingProjection.status == BookingStatus(status_filter).value)
        except ValueError:
            valid = [s.value for s in BookingStatus]
            raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {valid}")
    if user_id:
        query = query.where(AdminBookingProjection.user_id == user_id)
    if pandit_id:
        query = query.where(AdminBookingProjection.pandit_id == pandit_id)

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


@router.get("/analytics", response_model=AdminAnalyticsResponse)
async def get_analytics(
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
):
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total_users = await db.scalar(
        select(func.count(AdminUserProjection.user_id)).where(AdminUserProjection.role == UserRole.USER.value)
    )
    total_pandits = await db.scalar(select(func.count(AdminPanditReviewProjection.pandit_id)))
    verified_pandits = await db.scalar(
        select(func.count(AdminPanditReviewProjection.pandit_id)).where(
            AdminPanditReviewProjection.verification_status == VerificationStatus.VERIFIED.value
        )
    )
    pending_verification = await db.scalar(
        select(func.count(AdminPanditReviewProjection.pandit_id)).where(
            AdminPanditReviewProjection.verification_status == VerificationStatus.PENDING.value
        )
    )
    total_bookings = await db.scalar(select(func.count(AdminBookingProjection.booking_id)))
    bookings_today = await db.scalar(
        select(func.count(AdminBookingProjection.booking_id)).where(AdminBookingProjection.created_at >= today_start)
    )
    total_revenue = await db.scalar(
        select(func.sum(AdminPaymentProjection.amount)).where(AdminPaymentProjection.status == "CAPTURED")
    )
    revenue_today = await db.scalar(
        select(func.sum(AdminPaymentProjection.amount)).where(
            AdminPaymentProjection.status == "CAPTURED",
            AdminPaymentProjection.captured_at >= today_start,
        )
    )
    avg_rating = await db.scalar(
        select(func.avg(AdminReviewProjection.rating)).where(AdminReviewProjection.is_visible == True)
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


@router.get("/audit-logs")
async def get_audit_logs(
    action: str = Query(None, description="Filter by action type e.g. VERIFY_PANDIT"),
    entity_type: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(AdminAuditLog, AdminUserProjection)
        .join(AdminUserProjection, AdminUserProjection.user_id == AdminAuditLog.admin_id, isouter=True)
        .order_by(AdminAuditLog.created_at.desc())
    )
    if action:
        query = query.where(AdminAuditLog.action == action.upper())
    if entity_type:
        query = query.where(AdminAuditLog.entity_type == entity_type)

    count_query = select(AdminAuditLog)
    if action:
        count_query = count_query.where(AdminAuditLog.action == action.upper())
    if entity_type:
        count_query = count_query.where(AdminAuditLog.entity_type == entity_type)

    total = await db.scalar(select(func.count()).select_from(count_query.subquery()))
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    rows = result.all()

    return {
        "items": [
            {
                "id": str(row[0].id),
                "admin_name": row[1].name if row[1] else None,
                "admin_email": row[1].email if row[1] else None,
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
