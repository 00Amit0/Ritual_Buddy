"""
services/review/router.py
Rating and review management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from services.search.router import _get_pandit_coordinates, ensure_pandit_index, get_es_client, index_pandit
from shared.middleware.auth import get_current_user, require_admin
from shared.models.models import Booking, BookingStatus, PanditProfile, Review, User
from shared.schemas.schemas import MessageResponse, ReviewCreateRequest, ReviewResponse

router = APIRouter(prefix="/reviews", tags=["Reviews"])


async def _sync_pandit_search_index(db: AsyncSession, pandit_id: UUID) -> None:
    pandit_result = await db.execute(select(PanditProfile).where(PanditProfile.id == pandit_id))
    pandit = pandit_result.scalar_one_or_none()
    if not pandit:
        return

    user_result = await db.execute(select(User).where(User.id == pandit.user_id))
    user = user_result.scalar_one_or_none()
    es_client = await get_es_client()
    if not es_client or not user:
        return

    try:
        await ensure_pandit_index(es_client)
        latitude, longitude = await _get_pandit_coordinates(db, pandit)
        await index_pandit(
            es_client,
            pandit,
            user,
            latitude=latitude,
            longitude=longitude,
        )
    except Exception:
        pass
    finally:
        await es_client.close()


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
    await _sync_pandit_search_index(db, booking.pandit_id)

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
    await db.flush()

    # Recalculate rating excluding hidden review
    avg_result = await db.execute(
        select(func.avg(Review.rating), func.count(Review.id))
        .where(
            Review.pandit_id == review.pandit_id,
            Review.is_visible == True,
        )
    )
    avg, count = avg_result.one()
    await db.execute(
        update(PanditProfile)
        .where(PanditProfile.id == review.pandit_id)
        .values(rating_avg=round(float(avg or 0), 2), rating_count=count)
    )

    await db.commit()
    await _sync_pandit_search_index(db, review.pandit_id)
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
