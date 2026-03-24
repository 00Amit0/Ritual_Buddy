"""
services/review/router.py
Rating and review management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from shared.events.outbox import enqueue_event
from shared.middleware.auth import get_current_principal, require_admin_principal
from shared.models.models import Review, ReviewBookingProjection
from shared.schemas.schemas import MessageResponse, ReviewCreateRequest, ReviewResponse

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.post("", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
async def create_review(
    data: ReviewCreateRequest,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a review for a completed booking.
    - One review per booking (enforced by DB unique constraint)
    - Booking must be in COMPLETED status
    - Only the user who made the booking can review
    """
    booking = await db.get(ReviewBookingProjection, data.booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found in review projection")

    user_id = UUID(str(current_user.id))
    if booking.user_id != user_id:
        raise HTTPException(status_code=403, detail="You can only review your own bookings")

    if booking.status != "COMPLETED":
        raise HTTPException(status_code=400, detail="Booking must be completed before reviewing")

    existing = await db.execute(select(Review).where(Review.booking_id == data.booking_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="You have already reviewed this booking")

    review = Review(
        booking_id=data.booking_id,
        user_id=user_id,
        pandit_id=booking.pandit_id,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(review)
    await db.flush()

    avg_result = await db.execute(
        select(func.avg(Review.rating), func.count(Review.id)).where(
            Review.pandit_id == booking.pandit_id,
            Review.is_visible == True,
        )
    )
    avg, count = avg_result.one()
    rating_avg = round(float(avg or 0), 2)
    rating_count = count or 0

    await enqueue_event(
        db,
        topic="review-events",
        event_type="review.submitted",
        event_key=str(review.id),
        payload={
            "review_id": str(review.id),
            "booking_id": str(review.booking_id),
            "booking_number": booking.booking_number,
            "user_id": str(review.user_id),
            "pandit_id": str(review.pandit_id),
            "rating": review.rating,
            "comment": review.comment,
            "pandit_rating_avg": rating_avg,
            "pandit_rating_count": rating_count,
        },
    )

    await db.commit()
    return ReviewResponse.model_validate(review)


@router.put("/{review_id}/flag", response_model=MessageResponse)
async def flag_review(
    review_id: UUID,
    reason: str = Query(..., min_length=5, max_length=255),
    db: AsyncSession = Depends(get_db),
):
    """Flag a review for admin moderation (public endpoint, no auth required)."""
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    review.is_flagged = True
    review.flag_reason = reason
    await db.commit()
    return MessageResponse(message="Review has been flagged for moderation")


@router.delete("/{review_id}", response_model=MessageResponse)
async def delete_review(
    review_id: UUID,
    current_user=Depends(require_admin_principal),
    db: AsyncSession = Depends(get_db),
):
    """Admin: soft-delete a review (hides from public without removing from DB)."""
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    review.is_visible = False

    avg_result = await db.execute(
        select(func.avg(Review.rating), func.count(Review.id)).where(
            Review.pandit_id == review.pandit_id,
            Review.is_visible == True,
        )
    )
    avg, count = avg_result.one()
    rating_avg = round(float(avg or 0), 2)
    rating_count = count or 0

    await enqueue_event(
        db,
        topic="review-events",
        event_type="review.hidden",
        event_key=str(review.id),
        payload={
            "review_id": str(review.id),
            "pandit_id": str(review.pandit_id),
            "pandit_rating_avg": rating_avg,
            "pandit_rating_count": rating_count,
            "hidden_by": str(current_user.id),
        },
    )

    await db.commit()
    return MessageResponse(message="Review hidden successfully")


@router.get("/pandit/{pandit_id}", response_model=list[ReviewResponse])
async def get_pandit_reviews(
    pandit_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Public: get visible reviews for a specific pandit."""
    result = await db.execute(
        select(Review)
        .where(Review.pandit_id == pandit_id, Review.is_visible == True)
        .order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [ReviewResponse.model_validate(r) for r in result.scalars()]
