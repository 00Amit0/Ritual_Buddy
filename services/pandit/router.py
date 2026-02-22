"""
services/pandit/router.py
Pandit profile management: CRUD, availability, geo location, earnings.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from geoalchemy2.functions import ST_AsGeoJSON, ST_MakePoint, ST_SetSRID
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.redis_client import RedisCache, get_redis
from shared.middleware.auth import get_current_user, require_pandit
from shared.models.models import (
    Booking,
    BookingStatus,
    PanditAvailability,
    PanditProfile,
    Payment,
    PaymentStatus,
    User,
    UserRole,
    VerificationStatus,
)
from shared.schemas.schemas import (
    MessageResponse,
    PanditAvailabilityResponse,
    PanditAvailabilityUpdate,
    PanditProfileResponse,
    PanditProfileUpdate,
)

router = APIRouter(prefix="/pandits", tags=["Pandits"])


# ── Helpers ───────────────────────────────────────────────────

async def _get_pandit_or_404(pandit_id: UUID, db: AsyncSession) -> PanditProfile:
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.id == pandit_id)
    )
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")
    return pandit


async def _enrich_profile(pandit: PanditProfile, db: AsyncSession) -> PanditProfileResponse:
    """Add name, avatar from joined User, and extract lat/lng from PostGIS point."""
    result = await db.execute(select(User).where(User.id == pandit.user_id))
    user = result.scalar_one_or_none()

    lat, lng = None, None
    if pandit.location is not None:
        geo_json = await db.scalar(
            select(ST_AsGeoJSON(pandit.location))
        )
        if geo_json:
            coords = json.loads(geo_json)["coordinates"]
            lng, lat = coords[0], coords[1]

    return PanditProfileResponse(
        **{
            col.name: getattr(pandit, col.name)
            for col in PanditProfile.__table__.columns
            if col.name not in ("location",)
        },
        latitude=lat,
        longitude=lng,
        name=user.name if user else None,
        avatar_url=user.avatar_url if user else None,
    )


# ── Public Endpoints ──────────────────────────────────────────

@router.get("/{pandit_id}", response_model=PanditProfileResponse)
async def get_pandit(pandit_id: UUID, db: AsyncSession = Depends(get_db), redis=Depends(get_redis)):
    """Get a pandit's public profile. Cached for 5 minutes."""
    cache = RedisCache(redis)
    cache_key = f"pandit:{pandit_id}"

    cached = await cache.get(cache_key)
    if cached:
        return PanditProfileResponse(**cached)

    pandit = await _get_pandit_or_404(pandit_id, db)
    if pandit.verification_status != VerificationStatus.VERIFIED:
        raise HTTPException(status_code=404, detail="Pandit not found or not verified")

    profile = await _enrich_profile(pandit, db)
    await cache.set(cache_key, profile.model_dump())
    return profile


@router.get("/{pandit_id}/availability", response_model=List[PanditAvailabilityResponse])
async def get_pandit_availability(
    pandit_id: UUID,
    date: Optional[str] = Query(None, description="YYYY-MM-DD — if omitted returns next 7 days"),
    db: AsyncSession = Depends(get_db),
):
    """Get a pandit's available time slots for a specific date or next 7 days."""
    pandit = await _get_pandit_or_404(pandit_id, db)

    query = select(PanditAvailability).where(
        PanditAvailability.pandit_id == pandit.id,
        PanditAvailability.is_booked == False,
        PanditAvailability.is_blocked == False,
    )

    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d")
            query = query.where(func.date(PanditAvailability.date) == target_date.date())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        query = query.where(
            PanditAvailability.date >= now,
            PanditAvailability.date <= now + timedelta(days=7),
        )

    result = await db.execute(query.order_by(PanditAvailability.date))
    slots = result.scalars().all()
    return [PanditAvailabilityResponse.model_validate(s) for s in slots]


@router.get("/{pandit_id}/reviews")
async def get_pandit_reviews(
    pandit_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated public reviews for a pandit."""
    from shared.models.models import Review
    from shared.schemas.schemas import ReviewResponse

    pandit = await _get_pandit_or_404(pandit_id, db)

    query = (
        select(Review)
        .where(Review.pandit_id == pandit.id, Review.is_visible == True)
        .order_by(Review.created_at.desc())
    )
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    reviews = result.scalars().all()

    return {
        "items": [ReviewResponse.model_validate(r) for r in reviews],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── Pandit's Own Profile Endpoints ────────────────────────────

@router.get("/me/profile", response_model=PanditProfileResponse)
async def get_my_profile(
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
):
    """Get the authenticated pandit's own profile."""
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit profile not found. Please complete setup.")
    return await _enrich_profile(pandit, db)


@router.put("/me/profile", response_model=PanditProfileResponse)
async def update_my_profile(
    update_data: PanditProfileUpdate,
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Update pandit's own profile.
    If location provided, updates PostGIS point and Redis GEO.
    """
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = result.scalar_one_or_none()

    if not pandit:
        # Auto-create profile on first update
        pandit = PanditProfile(user_id=current_user.id)
        db.add(pandit)

    # Apply updates
    for field, value in update_data.model_dump(exclude_none=True, exclude={"latitude", "longitude"}).items():
        setattr(pandit, field, value)

    # Handle location update
    if update_data.latitude is not None and update_data.longitude is not None:
        pandit.location = ST_SetSRID(
            ST_MakePoint(update_data.longitude, update_data.latitude), 4326
        )
        # Update Redis GEO for real-time nearby queries
        cache = RedisCache(redis)
        await cache.add_pandit_location(
            str(pandit.id), update_data.longitude, update_data.latitude
        )

    # Check if profile is complete
    pandit.profile_complete = all([
        pandit.bio,
        pandit.experience_years >= 0,
        pandit.languages,
        pandit.poojas_offered,
        pandit.city,
        pandit.base_fee,
    ])

    await db.flush()

    # Invalidate cache
    cache = RedisCache(redis)
    await cache.delete(f"pandit:{pandit.id}")

    # TODO: Emit PanditUpdated event to Kafka → Elasticsearch re-index
    # await kafka_producer.send("pandit.updates", {...})

    profile = await _enrich_profile(pandit, db)
    return profile


@router.put("/me/availability", response_model=MessageResponse)
async def update_my_availability(
    data: PanditAvailabilityUpdate,
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
):
    """
    Set/replace availability slots for the pandit.
    If replace_date is provided, all existing slots for that date are deleted first.
    """
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit profile not found")

    if data.replace_date:
        try:
            target_date = datetime.strptime(data.replace_date, "%Y-%m-%d")
            await db.execute(
                delete(PanditAvailability).where(
                    PanditAvailability.pandit_id == pandit.id,
                    func.date(PanditAvailability.date) == target_date.date(),
                    PanditAvailability.is_booked == False,
                )
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid replace_date format")

    for slot in data.slots:
        try:
            slot_date = datetime.strptime(slot.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date: {slot.date}")

        # Check for conflicts with existing booked slots
        existing = await db.execute(
            select(PanditAvailability).where(
                PanditAvailability.pandit_id == pandit.id,
                func.date(PanditAvailability.date) == slot_date.date(),
                PanditAvailability.start_time == slot.start_time,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(PanditAvailability(
                pandit_id=pandit.id,
                date=slot_date,
                start_time=slot.start_time,
                end_time=slot.end_time,
            ))

    await db.commit()
    return MessageResponse(message=f"{len(data.slots)} availability slots updated")


@router.get("/me/calendar")
async def get_my_calendar(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2024),
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
):
    """Get calendar view: all bookings + availability for a given month."""
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit profile not found")

    import calendar
    _, last_day = calendar.monthrange(year, month)
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    # Get bookings
    bookings_result = await db.execute(
        select(Booking).where(
            Booking.pandit_id == pandit.id,
            Booking.scheduled_at >= start,
            Booking.scheduled_at <= end,
            Booking.status.in_([
                BookingStatus.CONFIRMED,
                BookingStatus.IN_PROGRESS,
                BookingStatus.AWAITING_PANDIT,
            ]),
        )
    )
    bookings = bookings_result.scalars().all()

    # Get availability
    slots_result = await db.execute(
        select(PanditAvailability).where(
            PanditAvailability.pandit_id == pandit.id,
            PanditAvailability.date >= start,
            PanditAvailability.date <= end,
        )
    )
    slots = slots_result.scalars().all()

    return {
        "year": year,
        "month": month,
        "bookings": [
            {
                "id": str(b.id),
                "booking_number": b.booking_number,
                "scheduled_at": b.scheduled_at.isoformat(),
                "duration_hrs": float(b.duration_hrs),
                "status": b.status.value,
            }
            for b in bookings
        ],
        "availability": [
            {
                "id": str(s.id),
                "date": s.date.date().isoformat(),
                "start_time": s.start_time,
                "end_time": s.end_time,
                "is_booked": s.is_booked,
                "is_blocked": s.is_blocked,
            }
            for s in slots
        ],
    }


@router.get("/me/earnings")
async def get_my_earnings(
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
):
    """Earnings summary: lifetime, current month, pending payout."""
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit profile not found")

    # Total lifetime earnings
    total_result = await db.execute(
        select(func.sum(Payment.payout_amount)).where(
            Payment.status == PaymentStatus.CAPTURED,
            Payment.booking_id.in_(
                select(Booking.id).where(Booking.pandit_id == pandit.id)
            ),
        )
    )
    total_earned = total_result.scalar() or 0

    # Pending (completed bookings, not yet paid out)
    pending_result = await db.execute(
        select(func.sum(Booking.pandit_payout)).where(
            Booking.pandit_id == pandit.id,
            Booking.status == BookingStatus.COMPLETED,
        )
    )
    pending_payout = pending_result.scalar() or 0

    # This month's bookings count
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    month_result = await db.execute(
        select(func.count()).where(
            Booking.pandit_id == pandit.id,
            Booking.status == BookingStatus.COMPLETED,
            Booking.completed_at >= month_start,
        )
    )
    bookings_this_month = month_result.scalar() or 0

    return {
        "total_earned": float(total_earned),
        "pending_payout": float(pending_payout),
        "bookings_this_month": bookings_this_month,
        "rating_avg": float(pandit.rating_avg),
        "rating_count": pandit.rating_count,
    }


@router.put("/me/location", response_model=MessageResponse)
async def update_my_location(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(require_pandit),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Real-time location update from pandit's mobile app (called every ~5min).
    Updates PostGIS + Redis GEO.
    """
    result = await db.execute(
        select(PanditProfile).where(PanditProfile.user_id == current_user.id)
    )
    pandit = result.scalar_one_or_none()
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit profile not found")

    pandit.location = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)

    # Update Redis GEO
    cache = RedisCache(redis)
    await cache.add_pandit_location(str(pandit.id), longitude, latitude)

    return MessageResponse(message="Location updated")
