"""
services/user/router.py
User profile management, saved pandits, and address book.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from shared.middleware.auth import get_current_user
from shared.models.models import PanditProfile, SavedPandit, User, UserAddress
from shared.schemas.schemas import (
    MessageResponse,
    UserAddressCreate,
    UserAddressResponse,
    UserResponse,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update user profile fields (name, phone, preferred_language, fcm_token).
    Only non-None fields in the request body are updated.
    """
    updates = data.model_dump(exclude_none=True)
    if not updates:
        return UserResponse.model_validate(current_user)

    # Phone uniqueness check
    if "phone" in updates:
        existing = await db.execute(
            select(User).where(User.phone == updates["phone"], User.id != current_user.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Phone number already in use")

    for field, value in updates.items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


# ── Saved Pandits (Favourites) ─────────────────────────────────────────────────

@router.get("/me/saved-pandits")
async def get_saved_pandits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the list of pandits saved/favourited by the current user."""
    result = await db.execute(
        select(SavedPandit, PanditProfile, User)
        .join(PanditProfile, PanditProfile.id == SavedPandit.pandit_id)
        .join(User, User.id == PanditProfile.user_id)
        .where(SavedPandit.user_id == current_user.id)
        .order_by(SavedPandit.created_at.desc())
    )
    rows = result.all()
    return [
        {
            "saved_at": row[0].created_at.isoformat(),
            "pandit_id": str(row[0].pandit_id),
            "name": row[2].name,
            "avatar_url": row[2].avatar_url,
            "city": row[1].city,
            "rating_avg": float(row[1].rating_avg or 0),
            "rating_count": row[1].rating_count or 0,
            "base_fee": float(row[1].base_fee or 0),
            "verification_status": row[1].verification_status.value,
        }
        for row in rows
    ]


@router.post("/me/saved-pandits/{pandit_id}", response_model=MessageResponse)
async def save_pandit(
    pandit_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save/favourite a pandit. Idempotent — saving twice is not an error."""
    # Verify pandit exists
    pandit = await db.scalar(select(PanditProfile).where(PanditProfile.id == pandit_id))
    if not pandit:
        raise HTTPException(status_code=404, detail="Pandit not found")

    existing = await db.scalar(
        select(SavedPandit).where(
            SavedPandit.user_id == current_user.id,
            SavedPandit.pandit_id == pandit_id,
        )
    )
    if existing:
        return MessageResponse(message="Already saved")

    db.add(SavedPandit(user_id=current_user.id, pandit_id=pandit_id))
    await db.commit()
    return MessageResponse(message="Pandit saved to favourites")


@router.delete("/me/saved-pandits/{pandit_id}", response_model=MessageResponse)
async def unsave_pandit(
    pandit_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a pandit from favourites."""
    await db.execute(
        delete(SavedPandit).where(
            SavedPandit.user_id == current_user.id,
            SavedPandit.pandit_id == pandit_id,
        )
    )
    await db.commit()
    return MessageResponse(message="Removed from favourites")


# ── Address Book ───────────────────────────────────────────────────────────────

@router.get("/me/addresses", response_model=list[UserAddressResponse])
async def get_addresses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all saved addresses for the current user."""
    result = await db.execute(
        select(UserAddress)
        .where(UserAddress.user_id == current_user.id)
        .order_by(UserAddress.is_default.desc(), UserAddress.created_at.desc())
    )
    return [UserAddressResponse.model_validate(a) for a in result.scalars()]


@router.post("/me/addresses", response_model=UserAddressResponse, status_code=status.HTTP_201_CREATED)
async def add_address(
    data: UserAddressCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a new address. If is_default=True, all existing defaults are cleared first."""
    if data.is_default:
        await db.execute(
            update(UserAddress)
            .where(UserAddress.user_id == current_user.id)
            .values(is_default=False)
        )

    address = UserAddress(
        user_id=current_user.id,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing address."""
    result = await db.execute(
        select(UserAddress).where(
            UserAddress.id == address_id,
            UserAddress.user_id == current_user.id,
        )
    )
    address = result.scalar_one_or_none()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")

    if data.is_default:
        await db.execute(
            update(UserAddress)
            .where(UserAddress.user_id == current_user.id, UserAddress.id != address_id)
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved address."""
    result = await db.execute(
        select(UserAddress).where(
            UserAddress.id == address_id,
            UserAddress.user_id == current_user.id,
        )
    )
    address = result.scalar_one_or_none()
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")

    await db.delete(address)
    await db.commit()
    return MessageResponse(message="Address deleted")
