"""
services/user/router.py
User profile management, saved pandits, and address book.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from shared.events.outbox import enqueue_event
from shared.middleware.auth import get_current_principal
from shared.models.models import SavedPandit, UserAccountProjection, UserAddress, UserPanditProjection
from shared.schemas.schemas import (
    MessageResponse,
    UserAddressCreate,
    UserAddressResponse,
    UserResponse,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["Users"])


def _serialize_user(user: UserAccountProjection) -> UserResponse:
    return UserResponse(
        id=user.user_id,
        email=user.email,
        name=user.name,
        phone=user.phone,
        avatar_url=user.avatar_url,
        role=user.role,
        preferred_language=user.preferred_language,
        created_at=user.created_at,
    )


async def _get_user_or_404(user_id: str, db: AsyncSession) -> UserAccountProjection:
    user = await db.get(UserAccountProjection, UUID(str(user_id)))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Return the currently authenticated user's profile."""
    user = await _get_user_or_404(current_user.id, db)
    return _serialize_user(user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdateRequest,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """
    Update user profile fields (name, phone, preferred_language, fcm_token).
    Only non-None fields in the request body are updated.
    """
    user = await _get_user_or_404(current_user.id, db)
    updates = data.model_dump(exclude_none=True)
    if not updates:
        return _serialize_user(user)

    await enqueue_event(
        db,
        topic="user-commands",
        event_type="user.profile_update_requested",
        event_key=str(user.id),
        payload={
            "user_id": str(user.id),
            "updates": updates,
        },
    )

    await db.commit()
    return _serialize_user(
        UserAccountProjection(
            user_id=user.user_id,
            email=user.email,
            name=updates.get("name", user.name),
            phone=updates.get("phone", user.phone),
            avatar_url=user.avatar_url,
            role=user.role,
            preferred_language=updates.get("preferred_language", user.preferred_language),
            fcm_token=updates.get("fcm_token", user.fcm_token),
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
    )


# ── Saved Pandits (Favourites) ─────────────────────────────────────────────────

@router.get("/me/saved-pandits")
async def get_saved_pandits(
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Get the list of pandits saved/favourited by the current user."""
    result = await db.execute(
        select(SavedPandit, UserPanditProjection)
        .join(UserPanditProjection, UserPanditProjection.pandit_id == SavedPandit.pandit_id)
        .where(SavedPandit.user_id == UUID(str(current_user.id)))
        .order_by(SavedPandit.created_at.desc())
    )
    rows = result.all()
    return [
        {
            "saved_at": saved.created_at.isoformat(),
            "pandit_id": str(saved.pandit_id),
            "name": projection.name,
            "avatar_url": projection.avatar_url,
            "city": projection.city,
            "rating_avg": float(projection.rating_avg or 0),
            "rating_count": projection.rating_count or 0,
            "base_fee": float(projection.base_fee or 0),
            "verification_status": projection.verification_status,
        }
        for saved, projection in rows
    ]


@router.post("/me/saved-pandits/{pandit_id}", response_model=MessageResponse)
async def save_pandit(
    pandit_id: UUID,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Save/favourite a pandit. Idempotent — saving twice is not an error."""
    # Verify pandit exists
    pandit = await db.scalar(
        select(UserPanditProjection).where(UserPanditProjection.pandit_id == pandit_id)
    )
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")

    existing = await db.scalar(
        select(SavedPandit).where(
            SavedPandit.user_id == UUID(str(current_user.id)),
            SavedPandit.pandit_id == pandit_id,
        )
    )
    if existing:
        return MessageResponse(message="Already saved")

    db.add(SavedPandit(user_id=UUID(str(current_user.id)), pandit_id=pandit_id))
    await db.commit()
    return MessageResponse(message="Pandit saved to favourites")


@router.delete("/me/saved-pandits/{pandit_id}", response_model=MessageResponse)
async def unsave_pandit(
    pandit_id: UUID,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Remove a pandit from favourites."""
    await db.execute(
        delete(SavedPandit).where(
            SavedPandit.user_id == UUID(str(current_user.id)),
            SavedPandit.pandit_id == pandit_id,
        )
    )
    await db.commit()
    return MessageResponse(message="Removed from favourites")


# ── Address Book ───────────────────────────────────────────────────────────────

@router.get("/me/addresses", response_model=list[UserAddressResponse])
async def get_addresses(
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Get all saved addresses for the current user."""
    result = await db.execute(
        select(UserAddress)
        .where(UserAddress.user_id == UUID(str(current_user.id)))
        .order_by(UserAddress.is_default.desc(), UserAddress.created_at.desc())
    )
    return [UserAddressResponse.model_validate(a) for a in result.scalars()]


@router.post("/me/addresses", response_model=UserAddressResponse, status_code=status.HTTP_201_CREATED)
async def add_address(
    data: UserAddressCreate,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Add a new address. If is_default=True, all existing defaults are cleared first."""
    if data.is_default:
        await db.execute(
            update(UserAddress)
            .where(UserAddress.user_id == UUID(str(current_user.id)))
            .values(is_default=False)
        )

    address = UserAddress(
        user_id=UUID(str(current_user.id)),
        **data.model_dump(exclude={"latitude", "longitude"}),
    )
    db.add(address)
    await db.commit()
    await db.refresh(address)
    return UserAddressResponse.model_validate(address)


@router.put("/me/addresses/{address_id}", response_model=UserAddressResponse)
async def update_address(
    address_id: UUID,
    data: UserAddressCreate,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing address."""
    result = await db.execute(
        select(UserAddress).where(
            UserAddress.id == address_id,
            UserAddress.user_id == UUID(str(current_user.id)),
        )
    )
    address = result.scalar_one_or_none()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")

    if data.is_default:
        await db.execute(
            update(UserAddress)
            .where(UserAddress.user_id == UUID(str(current_user.id)), UserAddress.id != address_id)
            .values(is_default=False)
        )

    for field, value in data.model_dump(exclude={"latitude", "longitude"}).items():
        setattr(address, field, value)

    await db.commit()
    await db.refresh(address)
    return UserAddressResponse.model_validate(address)


@router.delete("/me/addresses/{address_id}", response_model=MessageResponse)
async def delete_address(
    address_id: UUID,
    current_user=Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved address."""
    result = await db.execute(
        select(UserAddress).where(
            UserAddress.id == address_id,
            UserAddress.user_id == UUID(str(current_user.id)),
        )
    )
    address = result.scalar_one_or_none()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")

    await db.delete(address)
    await db.commit()
    return MessageResponse(message="Address deleted")
