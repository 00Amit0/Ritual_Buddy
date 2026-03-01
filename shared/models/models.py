"""
shared/models/models.py
All SQLAlchemy ORM models for the Pandit Booking Platform.
Uses PostGIS for geospatial data, UUID primary keys throughout.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import List, Optional

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


# ── Enumerations ──────────────────────────────────────────────

class UserRole(str, PyEnum):
    USER = "USER"
    PANDIT = "PANDIT"
    ADMIN = "ADMIN"


class OAuthProvider(str, PyEnum):
    GOOGLE = "GOOGLE"
    FACEBOOK = "FACEBOOK"


class VerificationStatus(str, PyEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


class BookingStatus(str, PyEnum):
    DRAFT = "DRAFT"
    SLOT_LOCKED = "SLOT_LOCKED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    AWAITING_PANDIT = "AWAITING_PANDIT"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DECLINED = "DECLINED"


class PaymentStatus(str, PyEnum):
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class NotificationType(str, PyEnum):
    BOOKING_CREATED = "BOOKING_CREATED"
    BOOKING_CONFIRMED = "BOOKING_CONFIRMED"
    BOOKING_DECLINED = "BOOKING_DECLINED"
    BOOKING_CANCELLED = "BOOKING_CANCELLED"
    BOOKING_COMPLETED = "BOOKING_COMPLETED"
    PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    REVIEW_RECEIVED = "REVIEW_RECEIVED"
    PAYOUT_SENT = "PAYOUT_SENT"
    ACCOUNT_VERIFIED = "ACCOUNT_VERIFIED"


class PoojaCategory(str, PyEnum):
    GRIHA = "GRIHA"           # Home rituals
    VIVAH = "VIVAH"           # Marriage
    JANAM = "JANAM"           # Birth ceremony
    MRITU = "MRITU"           # Death/memorial
    FESTIVAL = "FESTIVAL"     # Festival poojas
    BUSINESS = "BUSINESS"     # Business poojas
    HEALTH = "HEALTH"         # Health/wellness
    EDUCATION = "EDUCATION"   # Education/career
    OTHER = "OTHER"


# ── Mixins ────────────────────────────────────────────────────

class TimestampMixin:
    """Adds created_at and updated_at to any model."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds soft delete capability."""
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# ── Models ────────────────────────────────────────────────────

class User(TimestampMixin, SoftDeleteMixin, Base):
    """Core user account. Linked to OAuth provider."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    oauth_provider: Mapped[OAuthProvider] = mapped_column(
        Enum(OAuthProvider), nullable=False
    )
    oauth_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.USER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(10), default="hi", nullable=False)
    fcm_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Push notification token

    # Relationships
    pandit_profile: Mapped[Optional["PanditProfile"]] = relationship(
        back_populates="user", uselist=False, lazy="select", foreign_keys="PanditProfile.user_id"
    )
    bookings: Mapped[List["Booking"]] = relationship(
        back_populates="user", foreign_keys="Booking.user_id"
    )
    reviews: Mapped[List["Review"]] = relationship(back_populates="user")
    notifications: Mapped[List["Notification"]] = relationship(back_populates="user")
    saved_pandits: Mapped[List["SavedPandit"]] = relationship(back_populates="user")
    refresh_tokens: Mapped[List["RefreshToken"]] = relationship(back_populates="user")
    addresses: Mapped[List["UserAddress"]] = relationship(back_populates="user")

    __table_args__ = (
        UniqueConstraint("oauth_provider", "oauth_id", name="uq_oauth_provider_id"),
        Index("ix_users_email", "email"),
        Index("ix_users_role", "role"),
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class RefreshToken(Base):
    """Refresh tokens stored for rotation and revocation."""
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)


class UserAddress(TimestampMixin, Base):
    """User's saved addresses (for booking delivery)."""
    __tablename__ = "user_addresses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(50), nullable=False)  # "Home", "Office"
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(10), nullable=False)
    landmark: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    location = Column(Geography(geometry_type="POINT", srid=4326), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="addresses")


class Pooja(TimestampMixin, Base):
    """Master list of pooja/ritual types offered on the platform."""
    __tablename__ = "poojas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    name_hi: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_hi: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[PoojaCategory] = mapped_column(
        Enum(PoojaCategory), nullable=False, default=PoojaCategory.OTHER
    )
    avg_duration_hrs: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=2.0)
    samagri_list: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Items needed
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PanditProfile(TimestampMixin, Base):
    """
    Pandit's professional profile. PostGIS GEOGRAPHY for location.
    Links back to the User account (one-to-one).
    """
    __tablename__ = "pandit_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    experience_years: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    languages: Mapped[List[str]] = mapped_column(ARRAY(String(50)), default=list)
    poojas_offered: Mapped[List[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    # Location — PostGIS GEOGRAPHY point (lng, lat)
    location = Column(
        Geography(geometry_type="POINT", srid=4326),
        nullable=True,
        comment="Pandit's primary service location",
    )
    service_radius_km: Mapped[float] = mapped_column(Numeric(6, 2), default=25.0)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    country: Mapped[str] = mapped_column(String(50), default="India")

    # Pricing
    base_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=500.00)
    pooja_fees: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # e.g. {"pooja_uuid": 1500.00, ...}

    # Rating (denormalized for query performance)
    rating_avg: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0.00)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)

    # Verification
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus), default=VerificationStatus.PENDING, nullable=False
    )
    verification_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # Documents (S3 URLs stored in JSONB)
    documents: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # e.g. {"id_proof": "s3://...", "certificate": "s3://..."}

    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_complete: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    # user: Mapped["User"] = relationship(back_populates="pandit_profile")
    # availability_slots: Mapped[List["PanditAvailability"]] = relationship(
    #     back_populates="pandit"
    # )
    # bookings: Mapped[List["Booking"]] = relationship(
    #     back_populates="pandit", foreign_keys="Booking.pandit_id"
    # )
    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="pandit_profile",
        foreign_keys=[user_id]
    )

    verified_by: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[verified_by_id]
    )

    availability_slots: Mapped[List["PanditAvailability"]] = relationship(
        back_populates="pandit"
    )

    bookings: Mapped[List["Booking"]] = relationship(
        back_populates="pandit",
        foreign_keys="Booking.pandit_id"
    )

    __table_args__ = (
        Index("ix_pandit_profiles_user_id", "user_id"),
        Index("ix_pandit_profiles_city", "city"),
        Index("ix_pandit_profiles_verification", "verification_status"),
        Index("ix_pandit_profiles_location", "location", postgresql_using="gist"),
    )


class PanditAvailability(Base):
    """
    Pandit's available time slots. Set by pandit.
    Booked slots are marked is_booked=True when booking is confirmed.
    """
    __tablename__ = "pandit_availability"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    pandit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pandit_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_time: Mapped[str] = mapped_column(String(8), nullable=False)  # "09:00:00"
    end_time: Mapped[str] = mapped_column(String(8), nullable=False)    # "12:00:00"
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False)
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # Personal block (holiday etc)

    pandit: Mapped["PanditProfile"] = relationship(back_populates="availability_slots")

    __table_args__ = (
        Index("ix_availability_pandit_date", "pandit_id", "date"),
        Index("ix_availability_date", "date"),
    )


class Booking(TimestampMixin, Base):
    """
    Core booking entity. Managed via the Saga pattern.
    Status transitions: DRAFT → SLOT_LOCKED → PAYMENT_PENDING →
    AWAITING_PANDIT → CONFIRMED → IN_PROGRESS → COMPLETED | CANCELLED | DECLINED
    """
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    booking_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    pandit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pandit_profiles.id"), nullable=False
    )
    pooja_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("poojas.id"), nullable=False
    )

    # Schedule
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_hrs: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=2.0)
    accept_deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # Pandit must respond by this time

    # Status
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus), nullable=False, default=BookingStatus.DRAFT
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decline_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Location (where pooja will be performed)
    address: Mapped[dict] = mapped_column(JSONB, nullable=False)
    special_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pricing
    base_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0.00)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    pandit_payout: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0.00)

    # Timestamps
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    user: Mapped["User"] = relationship(back_populates="bookings", foreign_keys=[user_id])
    pandit: Mapped["PanditProfile"] = relationship(
        back_populates="bookings", foreign_keys=[pandit_id]
    )
    pooja: Mapped["Pooja"] = relationship()
    payment: Mapped[Optional["Payment"]] = relationship(back_populates="booking", uselist=False)
    review: Mapped[Optional["Review"]] = relationship(back_populates="booking", uselist=False)
    notifications: Mapped[List["Notification"]] = relationship(back_populates="booking")
    audit_logs: Mapped[List["BookingAuditLog"]] = relationship(back_populates="booking")

    __table_args__ = (
        Index("ix_bookings_user_id", "user_id"),
        Index("ix_bookings_pandit_id", "pandit_id"),
        Index("ix_bookings_status", "status"),
        Index("ix_bookings_scheduled_at", "scheduled_at"),
    )


class BookingAuditLog(Base):
    """Immutable log of all booking status transitions."""
    __tablename__ = "booking_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audit_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    booking: Mapped["Booking"] = relationship(back_populates="audit_logs")


class Payment(TimestampMixin, Base):
    """Payment transaction. Linked 1-to-1 with a booking."""
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bookings.id"),
        unique=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # Razorpay IDs
    razorpay_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    razorpay_signature: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Amounts (in paise for Razorpay, we store in INR)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0.00)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING
    )
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0.00)
    refund_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Payout tracking
    payout_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payout_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0.00)
    payout_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    booking: Mapped["Booking"] = relationship(back_populates="payment")

    __table_args__ = (
        Index("ix_payments_razorpay_order", "razorpay_order_id"),
        Index("ix_payments_razorpay_payment", "razorpay_payment_id"),
    )


class Review(TimestampMixin, Base):
    """Post-booking review. One per booking (enforced by unique constraint)."""
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    pandit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pandit_profiles.id"), nullable=False
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="reviews")
    booking: Mapped["Booking"] = relationship(back_populates="review")

    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating_range"),
        Index("ix_reviews_pandit_id", "pandit_id"),
        Index("ix_reviews_user_id", "user_id"),
    )


class Notification(TimestampMixin, Base):
    """In-app notification log. Sent via FCM, SMS, or email."""
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=True
    )
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_push: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_sms: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_email: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="notifications")
    booking: Mapped[Optional["Booking"]] = relationship(back_populates="notifications")

    __table_args__ = (Index("ix_notifications_user_id_read", "user_id", "is_read"),)


class SavedPandit(TimestampMixin, Base):
    """User's saved/favourite pandits."""
    __tablename__ = "saved_pandits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    pandit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pandit_profiles.id", ondelete="CASCADE"), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="saved_pandits")

    __table_args__ = (
        UniqueConstraint("user_id", "pandit_id", name="uq_saved_pandit"),
    )


class AdminAuditLog(Base):
    """Immutable log of all admin actions."""
    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_admin_audit_admin_id", "admin_id"),
        Index("ix_admin_audit_created_at", "created_at"),
    )
