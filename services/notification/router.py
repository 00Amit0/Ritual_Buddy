"""
services/notification/router.py
Notification read/update APIs for service-local notification records.
"""

from datetime import datetime, timezone
from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from shared.middleware.auth import get_current_principal
from shared.models.models import NotificationRecord
from shared.schemas.schemas import MessageResponse, NotificationResponse

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("")
async def get_my_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Get authenticated user's in-app notifications."""
    query = (
        select(NotificationRecord)
        .where(NotificationRecord.user_id == UUID(str(current_user.id)))
    )

    if unread_only:
        query = query.where(NotificationRecord.is_read.is_(False))

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    paged_query = (
        query.order_by(NotificationRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(paged_query)
    items = [NotificationResponse.model_validate(n) for n in result.scalars()]
    total_items = total or 0
    return {
        "items": items,
        "total": total_items,
        "page": page,
        "page_size": page_size,
        "pages": ceil(total_items / page_size) if total_items else 0,
    }


@router.post("/{notification_id}/read", response_model=MessageResponse)
async def mark_read(
    notification_id: UUID,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    notification = await db.scalar(
        select(NotificationRecord).where(NotificationRecord.id == notification_id)
    )
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if notification.user_id != UUID(str(current_user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    await db.execute(
        update(NotificationRecord)
        .where(NotificationRecord.id == notification_id)
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return MessageResponse(message="Marked as read")


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_read(
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(NotificationRecord)
        .where(
            NotificationRecord.user_id == UUID(str(current_user.id)),
            NotificationRecord.is_read.is_(False),
        )
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return MessageResponse(message="All notifications marked as read")


@router.get("/unread-count")
async def unread_count(
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    count = await db.scalar(
        select(func.count(NotificationRecord.id)).where(
            NotificationRecord.user_id == UUID(str(current_user.id)),
            NotificationRecord.is_read.is_(False),
        )
    )
    return {"unread_count": count or 0}
