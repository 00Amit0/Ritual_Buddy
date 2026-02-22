"""
services/review/router.py
Rating and review management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from shared.middleware.auth import get_current_user, require_admin
from shared.models.models import Booking, BookingStatus, PanditProfile, Review, User
from shared.schemas.schemas import MessageResponse, ReviewCreateRequest, ReviewResponse

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.post("", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
async def create_review(
    data: ReviewCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a review for a completed booking.
    - One review per booking (enforced by DB unique constraint)
    - Booking must be in COMPLETED status
    - Only the user who made the booking can review
    """
    booking_result = await db.execute(select(Booking).where(Booking.id == data.booking_id))
    booking = booking_result.scalar_one_or_none()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only review your own bookings")
    if booking.status != BookingStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Booking must be completed before reviewing")

    # Prevent duplicate reviews (belt + suspenders; DB unique constraint is the real guard)
    existing = await db.execute(select(Review).where(Review.booking_id == data.booking_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="You have already reviewed this booking")

    review = Review(
        booking_id=data.booking_id,
        user_id=current_user.id,
        pandit_id=booking.pandit_id,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(review)
    await db.flush()

    # Recalculate and denormalize aggregate rating on PanditProfile
    avg_result = await db.execute(
        select(func.avg(Review.rating), func.count(Review.id))
        .where(Review.pandit_id == booking.pandit_id, Review.is_visible == True)
    )
    avg, count = avg_result.one()

    await db.execute(
        update(PanditProfile)
        .where(PanditProfile.id == booking.pandit_id)
        .values(rating_avg=round(float(avg or 0), 2), rating_count=count)
    )

    await db.commit()

    # TODO: Emit ReviewSubmitted Kafka event → Elasticsearch re-index pandit with new rating

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
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: soft-delete a review (hides from public without removing from DB)."""
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    review.is_visible = False

    # Recalculate rating excluding hidden review
    avg_result = await db.execute(
        select(func.avg(Review.rating), func.count(Review.id))
        .where(Review.pandit_id == review.pandit_id, Review.is_visible == True)
    )
    avg, count = avg_result.one()
    await db.execute(
        update(PanditProfile)
        .where(PanditProfile.id == review.pandit_id)
        .values(rating_avg=round(float(avg or 0), 2), rating_count=count)
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
