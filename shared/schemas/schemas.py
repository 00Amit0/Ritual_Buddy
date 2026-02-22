"""
shared/schemas/schemas.py
All Pydantic v2 request/response schemas for the platform.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ── Base ──────────────────────────────────────────────────────

class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class PaginatedResponse(BaseSchema):
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int


# ── Auth ──────────────────────────────────────────────────────

class TokenResponse(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class AuthCallbackResponse(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserResponse"


# ── User ──────────────────────────────────────────────────────

class UserResponse(BaseSchema):
    id: uuid.UUID
    email: EmailStr
    name: str
    phone: Optional[str]
    avatar_url: Optional[str]
    role: str
    preferred_language: str
    created_at: datetime


class UserUpdateRequest(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone: Optional[str] = Field(None, pattern=r"^\+?[1-9]\d{9,14}$")
    preferred_language: Optional[str] = Field(None, max_length=10)
    fcm_token: Optional[str] = None


class UserAddressCreate(BaseSchema):
    label: str = Field(..., max_length=50)
    address_line1: str = Field(..., max_length=255)
    address_line2: Optional[str] = None
    city: str = Field(..., max_length=100)
    state: str = Field(..., max_length=100)
    pincode: str = Field(..., pattern=r"^\d{6}$")
    landmark: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    is_default: bool = False


class UserAddressResponse(UserAddressCreate):
    id: uuid.UUID


# ── Pooja ─────────────────────────────────────────────────────

class PoojaResponse(BaseSchema):
    id: uuid.UUID
    name_en: str
    name_hi: str
    slug: str
    description_en: Optional[str]
    description_hi: Optional[str]
    category: str
    avg_duration_hrs: Decimal
    image_url: Optional[str]


# ── Pandit ────────────────────────────────────────────────────

class PanditProfileResponse(BaseSchema):
    id: uuid.UUID
    user_id: uuid.UUID
    bio: Optional[str]
    experience_years: int
    languages: List[str]
    poojas_offered: List[uuid.UUID]
    service_radius_km: float
    city: Optional[str]
    state: Optional[str]
    base_fee: Decimal
    rating_avg: Decimal
    rating_count: int
    verification_status: str
    is_available: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Injected from User join
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    distance_km: Optional[float] = None  # From geo query


class PanditProfileUpdate(BaseSchema):
    bio: Optional[str] = Field(None, max_length=2000)
    experience_years: Optional[int] = Field(None, ge=0, le=70)
    languages: Optional[List[str]] = None
    poojas_offered: Optional[List[uuid.UUID]] = None
    service_radius_km: Optional[float] = Field(None, ge=1, le=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=10)
    base_fee: Optional[Decimal] = Field(None, ge=0)
    is_available: Optional[bool] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)


class PanditAvailabilitySlot(BaseSchema):
    date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM:SS")
    end_time: str = Field(..., description="HH:MM:SS")


class PanditAvailabilityUpdate(BaseSchema):
    slots: List[PanditAvailabilitySlot]
    replace_date: Optional[str] = None  # If set, replace all slots for this date


class PanditAvailabilityResponse(BaseSchema):
    id: uuid.UUID
    date: datetime
    start_time: str
    end_time: str
    is_booked: bool
    is_blocked: bool


# ── Search ────────────────────────────────────────────────────

class PanditSearchParams(BaseSchema):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(default=25.0, ge=1, le=500)
    pooja_id: Optional[uuid.UUID] = None
    languages: Optional[List[str]] = None
    experience_min: Optional[int] = Field(None, ge=0)
    experience_max: Optional[int] = Field(None, ge=0)
    price_min: Optional[Decimal] = None
    price_max: Optional[Decimal] = None
    available_date: Optional[str] = None  # YYYY-MM-DD
    sort_by: str = Field(default="distance", pattern="^(distance|rating|price|experience)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=50)


class PanditSearchResponse(BaseSchema):
    items: List[PanditProfileResponse]
    total: int
    page: int
    page_size: int


# ── Booking ───────────────────────────────────────────────────

class BookingAddressSchema(BaseSchema):
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    pincode: str
    landmark: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class BookingCreateRequest(BaseSchema):
    pandit_id: uuid.UUID
    pooja_id: uuid.UUID
    scheduled_at: datetime
    address: BookingAddressSchema
    special_requirements: Optional[str] = Field(None, max_length=1000)

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, v: datetime) -> datetime:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        if v <= now:
            raise ValueError("Scheduled time must be in the future")
        return v


class BookingResponse(BaseSchema):
    id: uuid.UUID
    booking_number: str
    user_id: uuid.UUID
    pandit_id: uuid.UUID
    pooja_id: uuid.UUID
    scheduled_at: datetime
    duration_hrs: Decimal
    status: str
    address: Dict[str, Any]
    special_requirements: Optional[str]
    base_amount: Decimal
    platform_fee: Decimal
    total_amount: Decimal
    accept_deadline: Optional[datetime]
    confirmed_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    cancellation_reason: Optional[str]
    created_at: datetime
    # Joined
    pandit_name: Optional[str] = None
    user_name: Optional[str] = None
    pooja_name: Optional[str] = None


class BookingDeclineRequest(BaseSchema):
    reason: str = Field(..., min_length=5, max_length=500)


class BookingCancelRequest(BaseSchema):
    reason: str = Field(..., min_length=5, max_length=500)


# ── Payment ───────────────────────────────────────────────────

class PaymentInitiateRequest(BaseSchema):
    booking_id: uuid.UUID


class PaymentInitiateResponse(BaseSchema):
    razorpay_order_id: str
    razorpay_key_id: str
    amount: int          # in paise
    currency: str
    booking_id: str


class PaymentVerifyRequest(BaseSchema):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    booking_id: uuid.UUID


class PaymentResponse(BaseSchema):
    id: uuid.UUID
    booking_id: uuid.UUID
    amount: Decimal
    status: str
    razorpay_payment_id: Optional[str]
    captured_at: Optional[datetime]
    created_at: datetime


# ── Review ────────────────────────────────────────────────────

class ReviewCreateRequest(BaseSchema):
    booking_id: uuid.UUID
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2000)


class ReviewResponse(BaseSchema):
    id: uuid.UUID
    booking_id: uuid.UUID
    user_id: uuid.UUID
    pandit_id: uuid.UUID
    rating: int
    comment: Optional[str]
    is_visible: bool
    created_at: datetime
    user_name: Optional[str] = None


# ── Notification ──────────────────────────────────────────────

class NotificationResponse(BaseSchema):
    id: uuid.UUID
    type: str
    title: str
    body: str
    is_read: bool
    read_at: Optional[datetime]
    created_at: datetime
    booking_id: Optional[uuid.UUID]


# ── Admin ─────────────────────────────────────────────────────

class AdminVerifyPanditRequest(BaseSchema):
    notes: Optional[str] = Field(None, max_length=1000)


class AdminRejectPanditRequest(BaseSchema):
    reason: str = Field(..., min_length=5, max_length=500)


class AdminSuspendRequest(BaseSchema):
    reason: str = Field(..., min_length=5, max_length=500)


class AdminAnalyticsResponse(BaseSchema):
    total_users: int
    total_pandits: int
    verified_pandits: int
    pending_verification: int
    total_bookings: int
    bookings_today: int
    total_revenue: Decimal
    revenue_today: Decimal
    avg_rating: float


# ── Generic ───────────────────────────────────────────────────

class MessageResponse(BaseSchema):
    message: str
    success: bool = True


class ErrorResponse(BaseSchema):
    detail: str
    code: Optional[str] = None
