"""Service event consumers (booking/payment/notification)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

from aiokafka import AIOKafkaConsumer
from sqlalchemy import delete, func, select

from config.database import AsyncSessionLocal
from config.settings import settings
from shared.events.outbox import enqueue_event
from shared.models.models import (
    AdminPanditReviewProjection,
    AdminBookingProjection,
    AdminPaymentProjection,
    AdminReviewProjection,
    AdminUserProjection,
    Booking,
    BookingAvailabilityProjection,
    BookingPanditProjection,
    BookingStatus,
    NotificationBookingProjection,
    NotificationRecord,
    NotificationType,
    Payment,
    PaymentBookingProjection,
    PanditProfile,
    PanditBookingProjection,
    PanditReviewProjection,
    PanditUserProjection,
    ReviewBookingProjection,
    OAuthProvider,
    SearchPanditAvailabilityProjection,
    SearchPanditProjection,
    UserAccountProjection,
    User,
    UserPanditProjection,
    UserRole,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


def _safe_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _safe_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def _upsert_notification_user(payload: dict[str, object]) -> None:
    user_id = _safe_uuid(payload.get("user_id"))
    if user_id is None:
        return

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        role_value = payload.get("role") or UserRole.USER.value
        oauth_provider_value = payload.get("oauth_provider") or OAuthProvider.GOOGLE.value

        if user is None:
            user = User(
                id=user_id,
                oauth_provider=OAuthProvider(oauth_provider_value),
                oauth_id=str(payload.get("oauth_id") or user_id),
                email=str(payload.get("email") or f"{user_id}@notification.local"),
                name=str(payload.get("name") or "User"),
                role=UserRole(role_value),
                is_active=bool(payload.get("is_active", True)),
                preferred_language=str(payload.get("preferred_language") or "hi"),
            )
            db.add(user)
        else:
            if payload.get("name") is not None:
                user.name = str(payload["name"])
            if payload.get("email") is not None:
                user.email = str(payload["email"])
            if payload.get("phone") is not None:
                user.phone = str(payload["phone"])
            if payload.get("avatar_url") is not None:
                user.avatar_url = str(payload["avatar_url"])
            if payload.get("preferred_language") is not None:
                user.preferred_language = str(payload["preferred_language"])
            if payload.get("fcm_token") is not None:
                user.fcm_token = str(payload["fcm_token"])
            if payload.get("is_active") is not None:
                user.is_active = bool(payload["is_active"])
            if payload.get("role") is not None:
                user.role = UserRole(str(payload["role"]))

        await db.commit()


async def _create_notification_record_and_dispatch(
    *,
    recipient_id: uuid.UUID,
    booking_id: uuid.UUID | None,
    notification_type: NotificationType,
    title: str,
    body: str,
    payload: dict[str, object],
) -> None:
    from tasks.notification_tasks import send_email, send_push_notification, send_sms

    async with AsyncSessionLocal() as db:
        user = await db.get(User, recipient_id)
        notification = NotificationRecord(
            user_id=recipient_id,
            booking_id=booking_id,
            type=notification_type,
            title=title,
            body=body,
            data=payload,
        )
        db.add(notification)
        await db.flush()

        if user and user.fcm_token:
            send_push_notification.delay(
                user.fcm_token,
                title,
                body,
                {
                    "notification_id": str(notification.id),
                    "booking_id": str(booking_id) if booking_id else None,
                    "type": notification_type.value,
                },
            )
            notification.sent_push = True

        if user and user.phone:
            send_sms.delay(user.phone, body)
            notification.sent_sms = True

        if user and user.email:
            send_email.delay(user.email, title, f"<p>{body}</p>")
            notification.sent_email = True

        await db.commit()


async def run_booking_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "payment-events",
        "pandit-events",
        "admin-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="booking-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")

            payload = event.get("payload", {})
            if event_type in {"pandit.updated", "pandit.verified", "pandit.rejected", "pandit.suspended", "pandit.reinstated"}:
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                user_id = _safe_uuid(payload.get("user_id"))
                if pandit_id is None or user_id is None:
                    continue

                async with AsyncSessionLocal() as db:
                    projection = await db.get(BookingPanditProjection, pandit_id)
                    if projection is None:
                        projection = BookingPanditProjection(
                            pandit_id=pandit_id,
                            user_id=user_id,
                            verification_status=str(payload.get("verification_status") or "PENDING"),
                            is_available=bool(payload.get("is_available", False)),
                            base_fee=payload.get("base_fee", 0) or 0,
                            pooja_fees=payload.get("pooja_fees") or {},
                            city=payload.get("city"),
                            state=payload.get("state"),
                            profile_complete=bool(payload.get("profile_complete", False)),
                        )
                        db.add(projection)
                    else:
                        projection.user_id = user_id
                        if payload.get("verification_status") is not None:
                            projection.verification_status = str(payload["verification_status"])
                        elif event_type == "pandit.verified":
                            projection.verification_status = "VERIFIED"
                        elif event_type == "pandit.rejected":
                            projection.verification_status = "REJECTED"
                        elif event_type == "pandit.suspended":
                            projection.verification_status = "SUSPENDED"
                        elif event_type == "pandit.reinstated":
                            projection.verification_status = "VERIFIED"

                        if payload.get("is_available") is not None:
                            projection.is_available = bool(payload["is_available"])
                        elif event_type in {"pandit.rejected", "pandit.suspended"}:
                            projection.is_available = False
                        elif event_type == "pandit.reinstated":
                            projection.is_available = True

                        if payload.get("base_fee") is not None:
                            projection.base_fee = payload["base_fee"]
                        if payload.get("pooja_fees") is not None:
                            projection.pooja_fees = payload["pooja_fees"]
                        if payload.get("city") is not None:
                            projection.city = payload.get("city")
                        if payload.get("state") is not None:
                            projection.state = payload.get("state")
                        if payload.get("profile_complete") is not None:
                            projection.profile_complete = bool(payload["profile_complete"])

                    await db.commit()
                continue

            if event_type == "pandit.availability_replaced":
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                replace_date = payload.get("replace_date")
                if pandit_id is None:
                    continue

                target_date = None
                if replace_date:
                    try:
                        from datetime import datetime

                        target_date = datetime.strptime(str(replace_date), "%Y-%m-%d").date()
                    except Exception:
                        continue

                async with AsyncSessionLocal() as db:
                    if target_date is not None:
                        await db.execute(
                            delete(BookingAvailabilityProjection).where(
                                BookingAvailabilityProjection.pandit_id == pandit_id,
                                func.date(BookingAvailabilityProjection.date) == target_date,
                                BookingAvailabilityProjection.is_booked.is_(False),
                            )
                        )
                    for slot in payload.get("slots", []):
                        slot_date = slot.get("date")
                        if not slot_date:
                            continue
                        try:
                            parsed_date = slot_date.replace("Z", "+00:00")
                            from datetime import datetime

                            date_value = datetime.fromisoformat(parsed_date)
                        except Exception:
                            continue
                        if target_date is None:
                            await db.execute(
                                delete(BookingAvailabilityProjection).where(
                                    BookingAvailabilityProjection.pandit_id == pandit_id,
                                    BookingAvailabilityProjection.date == date_value,
                                    BookingAvailabilityProjection.start_time == str(slot.get("start_time")),
                                    BookingAvailabilityProjection.is_booked.is_(False),
                                )
                            )
                        db.add(
                            BookingAvailabilityProjection(
                                pandit_id=pandit_id,
                                date=date_value,
                                start_time=str(slot.get("start_time")),
                                end_time=str(slot.get("end_time")),
                                is_booked=bool(slot.get("is_booked", False)),
                                blocked_reason=slot.get("blocked_reason"),
                                is_blocked=bool(slot.get("is_blocked", False)),
                            )
                        )
                    await db.commit()
                continue

            if event_type not in {"payment.captured", "payment.initiated", "payment.failed"}:
                continue

            booking_id = payload.get("booking_id")
            if not booking_id:
                continue
            booking_uuid = uuid.UUID(str(booking_id))

            async with AsyncSessionLocal() as db:
                booking = await db.scalar(select(Booking).where(Booking.id == booking_uuid))
                if not booking:
                    await db.commit()
                    continue

                if event_type == "payment.initiated" and booking.status == BookingStatus.SLOT_LOCKED:
                    booking.status = BookingStatus.PAYMENT_PENDING
                    await enqueue_event(
                        db,
                        topic="booking-events",
                        event_type="booking.payment_pending",
                        event_key=str(booking.id),
                        payload={
                            "booking_id": str(booking.id),
                            "booking_number": booking.booking_number,
                            "status": booking.status.value,
                        },
                    )
                elif event_type == "payment.failed" and booking.status in (
                    BookingStatus.SLOT_LOCKED,
                    BookingStatus.PAYMENT_PENDING,
                ):
                    booking.status = BookingStatus.CANCELLED
                    booking.cancellation_reason = payload.get("reason", "Payment failed")
                    await enqueue_event(
                        db,
                        topic="booking-events",
                        event_type="booking.cancelled",
                        event_key=str(booking.id),
                        payload={
                            "booking_id": str(booking.id),
                            "booking_number": booking.booking_number,
                            "user_id": str(booking.user_id),
                            "pandit_id": str(booking.pandit_id),
                            "reason": booking.cancellation_reason,
                            "cancelled_by": "system",
                        },
                    )
                elif event_type == "payment.captured" and booking.status in (
                    BookingStatus.SLOT_LOCKED,
                    BookingStatus.PAYMENT_PENDING,
                ):
                    booking.status = BookingStatus.AWAITING_PANDIT
                    pandit_projection = await db.get(BookingPanditProjection, booking.pandit_id)
                    await enqueue_event(
                        db,
                        topic="booking-events",
                        event_type="booking.awaiting_pandit",
                        event_key=str(booking.id),
                        payload={
                            "booking_id": str(booking.id),
                            "pandit_id": str(booking.pandit_id),
                            "pandit_user_id": str(pandit_projection.user_id) if pandit_projection else None,
                            "user_id": str(booking.user_id),
                            "booking_number": booking.booking_number,
                            "scheduled_at": booking.scheduled_at.isoformat(),
                        },
                    )
                await db.commit()
    finally:
        await consumer.stop()


async def run_payment_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "booking-events",
        "payment-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="payment-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type == "payment.refund_requested":
                payment_id = payload.get("payment_id")
                if payment_id:
                    from tasks.payment_tasks import process_refund

                    process_refund.delay(
                        str(payment_id),
                        payload.get("amount"),
                        payload.get("reason", "Requested by event"),
                    )
                continue

            if event_type == "payment.payout_requested":
                payment_id = payload.get("payment_id")
                if payment_id:
                    from tasks.payment_tasks import process_single_payout

                    process_single_payout.delay(str(payment_id))
                continue

            booking_id = payload.get("booking_id")
            if not booking_id:
                continue
            booking_uuid = uuid.UUID(str(booking_id))

            async with AsyncSessionLocal() as db:
                if event_type == "booking.created":
                    existing = await db.get(PaymentBookingProjection, booking_uuid)
                    if not existing:
                        db.add(
                            PaymentBookingProjection(
                                booking_id=booking_uuid,
                                booking_number=payload.get("booking_number"),
                                user_id=uuid.UUID(str(payload.get("user_id"))),
                                pandit_id=uuid.UUID(str(payload.get("pandit_id"))),
                                total_amount=payload.get("total_amount"),
                                platform_fee=payload.get("platform_fee", 0),
                                status=payload.get("status", "SLOT_LOCKED"),
                            )
                        )
                    await db.commit()
                    continue

                projection = await db.get(PaymentBookingProjection, booking_uuid)
                if projection and event_type.startswith("booking."):
                    status_map = {
                        "booking.payment_pending": "PAYMENT_PENDING",
                        "booking.awaiting_pandit": "AWAITING_PANDIT",
                        "booking.confirmed": "CONFIRMED",
                        "booking.declined": "DECLINED",
                        "booking.cancelled": "CANCELLED",
                        "booking.completed": "COMPLETED",
                    }
                    new_status = status_map.get(event_type)
                    if new_status:
                        projection.status = new_status

                payment = await db.scalar(select(Payment).where(Payment.booking_id == booking_uuid))
                await db.commit()

            if not payment:
                continue

            if event_type in {"booking.declined", "booking.cancelled"}:
                async with AsyncSessionLocal() as db:
                    await enqueue_event(
                        db,
                        topic="payment-events",
                        event_type="payment.refund_requested",
                        event_key=str(payment.id),
                        payload={
                            "payment_id": str(payment.id),
                            "booking_id": str(booking_uuid),
                            "reason": f"Triggered by {event_type}",
                        },
                    )
                    await db.commit()
            elif event_type == "booking.completed":
                async with AsyncSessionLocal() as db:
                    await enqueue_event(
                        db,
                        topic="payment-events",
                        event_type="payment.payout_requested",
                        event_key=str(payment.id),
                        payload={
                            "payment_id": str(payment.id),
                            "booking_id": str(booking_uuid),
                        },
                    )
                    await db.commit()
    finally:
        await consumer.stop()


async def run_notification_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "booking-events",
        "payment-events",
        "admin-events",
        "review-events",
        "user-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="notification-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})
            if event_type in {"user.upserted", "user.updated", "user.suspended", "user.reactivated"}:
                await _upsert_notification_user(payload)
                continue

            if event_type == "booking.created":
                booking_id = _safe_uuid(payload.get("booking_id"))
                user_id = _safe_uuid(payload.get("user_id"))
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                scheduled_at = _safe_datetime(payload.get("scheduled_at"))
                if booking_id and user_id and pandit_id and scheduled_at:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(NotificationBookingProjection, booking_id)
                        if projection is None:
                            projection = NotificationBookingProjection(
                                booking_id=booking_id,
                                booking_number=str(payload.get("booking_number") or booking_id),
                                user_id=user_id,
                                pandit_id=pandit_id,
                                pandit_user_id=_safe_uuid(payload.get("pandit_user_id")),
                                status=str(payload.get("status") or "SLOT_LOCKED"),
                                scheduled_at=scheduled_at,
                            )
                            db.add(projection)
                        elif payload.get("pandit_user_id") is not None:
                            projection.pandit_user_id = _safe_uuid(payload.get("pandit_user_id"))
                        await db.commit()
                continue

            if event_type in {
                "booking.payment_pending",
                "booking.awaiting_pandit",
                "booking.confirmed",
                "booking.declined",
                "booking.cancelled",
                "booking.completed",
            }:
                booking_id = _safe_uuid(payload.get("booking_id"))
                if booking_id:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(NotificationBookingProjection, booking_id)
                        if projection:
                            status_map = {
                                "booking.payment_pending": "PAYMENT_PENDING",
                                "booking.awaiting_pandit": "AWAITING_PANDIT",
                                "booking.confirmed": "CONFIRMED",
                                "booking.declined": "DECLINED",
                                "booking.cancelled": "CANCELLED",
                                "booking.completed": "COMPLETED",
                            }
                            projection.status = status_map.get(event_type, projection.status)
                            if payload.get("scheduled_at") is not None:
                                scheduled_at = _safe_datetime(payload.get("scheduled_at"))
                                if scheduled_at is not None:
                                    projection.scheduled_at = scheduled_at
                            if payload.get("pandit_user_id") is not None:
                                projection.pandit_user_id = _safe_uuid(payload.get("pandit_user_id"))
                            if event_type == "booking.completed":
                                projection.completed_at = _safe_datetime(payload.get("completed_at")) or _safe_datetime(
                                    event.get("occurred_at")
                                )
                            await db.commit()

            if event_type == "review.submitted":
                booking_id = _safe_uuid(payload.get("booking_id"))
                if booking_id:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(NotificationBookingProjection, booking_id)
                        if projection:
                            projection.review_submitted_at = _safe_datetime(event.get("occurred_at"))
                            await db.commit()

            booking_id = None
            booking_id_raw = payload.get("booking_id")
            if booking_id_raw:
                try:
                    booking_id = uuid.UUID(str(booking_id_raw))
                except (ValueError, TypeError):
                    logger.warning("Skipping notification event with invalid booking_id: %s", booking_id_raw)
                    continue

            recipient_id_raw = payload.get("user_id")
            notification_type = None
            title = None
            body = None

            if event_type == "booking.confirmed":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.BOOKING_CONFIRMED
                title = "Booking Confirmed"
                body = f"Your booking {payload.get('booking_number', '')} is confirmed."
            elif event_type == "booking.awaiting_pandit":
                recipient_id_raw = payload.get("pandit_user_id") or payload.get("pandit_id")
                notification_type = NotificationType.BOOKING_CREATED
                title = "New Booking Request"
                body = f"You have a new booking {payload.get('booking_number', '')} to review."
            elif event_type == "booking.declined":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.BOOKING_DECLINED
                body_reason = payload.get("reason") or "The pandit declined your booking."
                title = "Booking Declined"
                body = body_reason
            elif event_type == "booking.cancelled":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.BOOKING_CANCELLED
                body_reason = payload.get("reason") or "Your booking was cancelled."
                title = "Booking Cancelled"
                body = body_reason
            elif event_type == "booking.completed":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.BOOKING_COMPLETED
                title = "Booking Completed"
                body = f"Booking {payload.get('booking_number', '')} is marked completed."
            elif event_type == "payment.captured":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.PAYMENT_SUCCESS
                amount = payload.get("amount")
                title = "Payment Successful"
                body = f"Payment of Rs. {amount} received for booking {payload.get('booking_number', '')}."
            elif event_type == "payment.failed":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.PAYMENT_FAILED
                title = "Payment Failed"
                body = payload.get("reason") or "Payment failed. Please retry."
            elif event_type == "payment.refunded":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.BOOKING_CANCELLED
                title = "Refund Processed"
                amount = payload.get("amount")
                body = f"Refund of Rs. {amount} has been processed for booking {payload.get('booking_number', '')}."
            elif event_type == "payment.payout_processed":
                recipient_id_raw = payload.get("pandit_user_id")
                notification_type = NotificationType.PAYOUT_SENT
                title = "Payout Processed"
                amount = payload.get("amount")
                body = f"Payout of Rs. {amount} has been processed for booking {payload.get('booking_number', '')}."
            elif event_type == "pandit.verified":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.ACCOUNT_VERIFIED
                title = "Profile Verified"
                body = "Your pandit profile has been verified. You can now accept bookings."
            elif event_type == "pandit.rejected":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.ACCOUNT_VERIFIED
                title = "Profile Review Update"
                body = payload.get("reason") or "Your profile application was not approved."
            elif event_type == "pandit.suspended":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.ACCOUNT_VERIFIED
                title = "Profile Suspended"
                body = payload.get("reason") or "Your pandit profile has been suspended."
            elif event_type == "pandit.reinstated":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.ACCOUNT_VERIFIED
                title = "Profile Reinstated"
                body = "Your pandit profile has been reinstated."
            elif event_type == "review.submitted":
                booking_projection = None
                if booking_id is not None:
                    async with AsyncSessionLocal() as db:
                        booking_projection = await db.get(NotificationBookingProjection, booking_id)
                recipient_id_raw = (
                    str(booking_projection.pandit_user_id)
                    if booking_projection and booking_projection.pandit_user_id
                    else payload.get("pandit_id")
                )
                notification_type = NotificationType.REVIEW_RECEIVED
                title = "New Review Received"
                body = f"You received a {payload.get('rating', '')}-star review."
            elif event_type == "user.suspended":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.ACCOUNT_VERIFIED
                title = "Account Suspended"
                body = payload.get("reason") or "Your account has been suspended."
            elif event_type == "user.reactivated":
                recipient_id_raw = payload.get("user_id")
                notification_type = NotificationType.ACCOUNT_VERIFIED
                title = "Account Reactivated"
                body = "Your account has been reactivated."

            if not notification_type or not recipient_id_raw:
                continue

            try:
                recipient_id = uuid.UUID(str(recipient_id_raw))
            except (ValueError, TypeError):
                logger.warning("Skipping notification event with invalid recipient id: %s", recipient_id_raw)
                continue

            await _create_notification_record_and_dispatch(
                recipient_id=recipient_id,
                booking_id=booking_id,
                notification_type=notification_type,
                title=title,
                body=body,
                payload=payload,
            )
    finally:
        await consumer.stop()


async def run_review_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "booking-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="review-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})
            booking_id = payload.get("booking_id")
            if not booking_id:
                continue
            booking_uuid = uuid.UUID(str(booking_id))

            async with AsyncSessionLocal() as db:
                if event_type == "booking.created":
                    existing = await db.get(ReviewBookingProjection, booking_uuid)
                    if not existing:
                        db.add(
                            ReviewBookingProjection(
                                booking_id=booking_uuid,
                                booking_number=payload.get("booking_number"),
                                user_id=uuid.UUID(str(payload.get("user_id"))),
                                pandit_id=uuid.UUID(str(payload.get("pandit_id"))),
                                status=payload.get("status", "SLOT_LOCKED"),
                            )
                        )
                    await db.commit()
                    continue

                projection = await db.get(ReviewBookingProjection, booking_uuid)
                if not projection:
                    await db.commit()
                    continue

                status_map = {
                    "booking.payment_pending": "PAYMENT_PENDING",
                    "booking.awaiting_pandit": "AWAITING_PANDIT",
                    "booking.confirmed": "CONFIRMED",
                    "booking.declined": "DECLINED",
                    "booking.cancelled": "CANCELLED",
                    "booking.completed": "COMPLETED",
                }
                new_status = status_map.get(event_type)
                if new_status:
                    projection.status = new_status

                await db.commit()
    finally:
        await consumer.stop()


async def run_search_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "pandit-events",
        "admin-events",
        "review-events",
        "user-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="search-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    es = None
    try:
        try:
            from elasticsearch import AsyncElasticsearch

            es = AsyncElasticsearch(
                settings.ELASTICSEARCH_URL,
                basic_auth=(
                    settings.ELASTICSEARCH_USERNAME or "elastic",
                    settings.ELASTICSEARCH_PASSWORD or "",
                ) if settings.ELASTICSEARCH_PASSWORD else None,
            )
        except Exception as exc:
            logger.warning("Search consumer cannot connect Elasticsearch: %s", exc)
            return

        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})
            pandit_id = payload.get("pandit_id")

            index = settings.ELASTICSEARCH_INDEX_PANDITS
            partial_doc: dict[str, object] = {}

            if event_type == "pandit.updated":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                user_uuid = _safe_uuid(payload.get("user_id"))
                if pandit_uuid and user_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection is None:
                            projection = SearchPanditProjection(
                                pandit_id=pandit_uuid,
                                user_id=user_uuid,
                            )
                            db.add(projection)
                        projection.user_id = user_uuid
                        projection.bio = payload.get("bio")
                        projection.languages = payload.get("languages") or []
                        projection.poojas_offered = [
                            _safe_uuid(item) for item in (payload.get("poojas_offered") or []) if _safe_uuid(item)
                        ]
                        projection.city = payload.get("city")
                        projection.state = payload.get("state")
                        if payload.get("latitude") is not None:
                            projection.latitude = payload.get("latitude")
                        if payload.get("longitude") is not None:
                            projection.longitude = payload.get("longitude")
                        if payload.get("rating_avg") is not None:
                            projection.rating_avg = payload.get("rating_avg")
                        if payload.get("rating_count") is not None:
                            projection.rating_count = payload.get("rating_count")
                        if payload.get("experience_years") is not None:
                            projection.experience_years = payload.get("experience_years")
                        if payload.get("base_fee") is not None:
                            projection.base_fee = payload.get("base_fee")
                        if payload.get("service_radius_km") is not None:
                            projection.service_radius_km = payload.get("service_radius_km")
                        if payload.get("is_available") is not None:
                            projection.is_available = bool(payload.get("is_available"))
                        if payload.get("verification_status") is not None:
                            projection.verification_status = str(payload.get("verification_status"))
                        await db.commit()
                partial_doc["id"] = pandit_id
                if payload.get("name") is not None:
                    partial_doc["name"] = payload.get("name")
                if payload.get("bio") is not None:
                    partial_doc["bio"] = payload.get("bio")
                if payload.get("languages") is not None:
                    partial_doc["languages"] = payload.get("languages")
                if payload.get("poojas_offered") is not None:
                    partial_doc["poojas_offered"] = payload.get("poojas_offered")
                if payload.get("city") is not None:
                    partial_doc["city"] = payload.get("city")
                if payload.get("state") is not None:
                    partial_doc["state"] = payload.get("state")
                if payload.get("latitude") is not None and payload.get("longitude") is not None:
                    partial_doc["location"] = {"lat": payload.get("latitude"), "lon": payload.get("longitude")}
                if payload.get("rating_avg") is not None:
                    partial_doc["rating_avg"] = payload.get("rating_avg")
                if payload.get("rating_count") is not None:
                    partial_doc["rating_count"] = payload.get("rating_count")
                if payload.get("experience_years") is not None:
                    partial_doc["experience_years"] = payload.get("experience_years")
                if payload.get("base_fee") is not None:
                    partial_doc["base_fee"] = payload.get("base_fee")
                if payload.get("service_radius_km") is not None:
                    partial_doc["service_radius_km"] = payload.get("service_radius_km")
                if payload.get("is_available") is not None:
                    partial_doc["is_available"] = payload.get("is_available")
                if payload.get("verification_status") is not None:
                    partial_doc["verification_status"] = payload.get("verification_status")
            elif event_type == "pandit.verified":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if pandit_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection:
                            projection.verification_status = "VERIFIED"
                            projection.is_available = True
                            await db.commit()
                partial_doc["id"] = pandit_id
                partial_doc["verification_status"] = "VERIFIED"
                partial_doc["is_available"] = True
            elif event_type == "pandit.rejected":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if pandit_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection:
                            projection.verification_status = "REJECTED"
                            projection.is_available = False
                            await db.commit()
                partial_doc["id"] = pandit_id
                partial_doc["verification_status"] = "REJECTED"
                partial_doc["is_available"] = False
            elif event_type == "pandit.suspended":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if pandit_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection:
                            projection.verification_status = "SUSPENDED"
                            projection.is_available = False
                            await db.commit()
                partial_doc["id"] = pandit_id
                partial_doc["verification_status"] = "SUSPENDED"
                partial_doc["is_available"] = False
            elif event_type == "pandit.reinstated":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if pandit_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection:
                            projection.verification_status = "VERIFIED"
                            projection.is_available = True
                            await db.commit()
                partial_doc["id"] = pandit_id
                partial_doc["verification_status"] = "VERIFIED"
                partial_doc["is_available"] = True
            elif event_type == "pandit.location_updated":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if pandit_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection:
                            if payload.get("latitude") is not None:
                                projection.latitude = payload.get("latitude")
                            if payload.get("longitude") is not None:
                                projection.longitude = payload.get("longitude")
                            await db.commit()
                partial_doc["id"] = pandit_id
                if payload.get("latitude") is not None and payload.get("longitude") is not None:
                    partial_doc["location"] = {"lat": payload.get("latitude"), "lon": payload.get("longitude")}
            elif event_type == "pandit.availability_replaced":
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if not pandit_uuid:
                    continue
                replace_date = payload.get("replace_date")
                target_date = None
                if replace_date:
                    try:
                        from datetime import datetime

                        target_date = datetime.strptime(str(replace_date), "%Y-%m-%d").date()
                    except Exception:
                        target_date = None
                async with AsyncSessionLocal() as db:
                    if target_date is not None:
                        await db.execute(
                            delete(SearchPanditAvailabilityProjection).where(
                                SearchPanditAvailabilityProjection.pandit_id == pandit_uuid,
                                func.date(SearchPanditAvailabilityProjection.date) == target_date,
                            )
                        )
                    for slot in payload.get("slots", []):
                        slot_date = slot.get("date")
                        if not slot_date:
                            continue
                        try:
                            from datetime import datetime

                            date_value = datetime.fromisoformat(str(slot_date).replace("Z", "+00:00"))
                        except Exception:
                            continue
                        if target_date is None:
                            await db.execute(
                                delete(SearchPanditAvailabilityProjection).where(
                                    SearchPanditAvailabilityProjection.pandit_id == pandit_uuid,
                                    SearchPanditAvailabilityProjection.date == date_value,
                                    SearchPanditAvailabilityProjection.start_time == str(slot.get("start_time")),
                                )
                            )
                        db.add(
                            SearchPanditAvailabilityProjection(
                                pandit_id=pandit_uuid,
                                date=date_value,
                                start_time=str(slot.get("start_time")),
                                end_time=str(slot.get("end_time")),
                                is_booked=bool(slot.get("is_booked", False)),
                                is_blocked=bool(slot.get("is_blocked", False)),
                            )
                        )
                    await db.commit()
                continue
            elif event_type in {"review.submitted", "review.hidden"}:
                if not pandit_id:
                    continue
                pandit_uuid = _safe_uuid(pandit_id)
                if pandit_uuid:
                    async with AsyncSessionLocal() as db:
                        projection = await db.get(SearchPanditProjection, pandit_uuid)
                        if projection:
                            if payload.get("pandit_rating_avg") is not None:
                                projection.rating_avg = payload.get("pandit_rating_avg")
                            if payload.get("pandit_rating_count") is not None:
                                projection.rating_count = payload.get("pandit_rating_count")
                            await db.commit()
                partial_doc["id"] = pandit_id
                if payload.get("pandit_rating_avg") is not None:
                    partial_doc["rating_avg"] = payload.get("pandit_rating_avg")
                if payload.get("pandit_rating_count") is not None:
                    partial_doc["rating_count"] = payload.get("pandit_rating_count")
            elif event_type in {"user.updated", "user.upserted"}:
                user_id = payload.get("user_id")
                if not user_id:
                    continue
                user_updates: dict[str, object] = {}
                if payload.get("name") is not None:
                    user_updates["name"] = payload.get("name")
                if payload.get("avatar_url") is not None:
                    user_updates["avatar_url"] = payload.get("avatar_url")
                user_uuid = _safe_uuid(user_id)
                if user_uuid:
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(
                            select(SearchPanditProjection).where(SearchPanditProjection.user_id == user_uuid)
                        )
                        for projection in result.scalars():
                            if payload.get("name") is not None:
                                projection.name = payload.get("name")
                            if payload.get("avatar_url") is not None:
                                projection.avatar_url = payload.get("avatar_url")
                        await db.commit()
                if not user_updates:
                    continue
                try:
                    result = await es.search(
                        index=index,
                        body={"query": {"term": {"user_id": str(user_id)}}},
                        size=100,
                    )
                    for hit in result.get("hits", {}).get("hits", []):
                        await es.update(
                            index=index,
                            id=hit["_id"],
                            body={"doc": user_updates, "doc_as_upsert": False},
                        )
                except Exception as exc:
                    logger.warning("Search consumer failed user-event indexing for user %s: %s", user_id, exc)
                continue
            else:
                continue

            try:
                await es.update(
                    index=index,
                    id=str(pandit_id),
                    body={"doc": partial_doc, "doc_as_upsert": True},
                )
            except Exception as exc:
                logger.warning("Search consumer failed indexing event %s for pandit %s: %s", event_type, pandit_id, exc)
    finally:
        try:
            if es is not None:
                await es.close()
        except Exception:
            pass
        await consumer.stop()


async def run_pandit_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "booking-events",
        "payment-events",
        "admin-commands",
        "user-events",
        "review-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="pandit-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type in {"user.upserted", "user.updated"}:
                user_id = _safe_uuid(payload.get("user_id"))
                if user_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(PanditUserProjection, user_id)
                    if projection is None:
                        projection = PanditUserProjection(user_id=user_id)
                        db.add(projection)
                    if payload.get("name") is not None:
                        projection.name = str(payload.get("name"))
                    if payload.get("avatar_url") is not None:
                        projection.avatar_url = payload.get("avatar_url")
                    if payload.get("email") is not None:
                        projection.email = str(payload.get("email"))
                    if payload.get("phone") is not None:
                        projection.phone = payload.get("phone")
                    if payload.get("is_active") is not None:
                        projection.is_active = bool(payload.get("is_active"))
                    await db.commit()
                continue

            if event_type == "review.submitted":
                review_id = _safe_uuid(payload.get("review_id"))
                booking_id = _safe_uuid(payload.get("booking_id"))
                user_id = _safe_uuid(payload.get("user_id"))
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                if review_id is None or booking_id is None or user_id is None or pandit_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(PanditReviewProjection, review_id)
                    if projection is None:
                        projection = PanditReviewProjection(
                            review_id=review_id,
                            booking_id=booking_id,
                            user_id=user_id,
                            pandit_id=pandit_id,
                            rating=int(payload.get("rating") or 0),
                            is_visible=True,
                        )
                        db.add(projection)
                    projection.booking_number = payload.get("booking_number")
                    projection.comment = payload.get("comment")
                    projection.rating = int(payload.get("rating") or projection.rating)
                    projection.is_visible = True
                    await db.commit()
                continue

            if event_type == "review.hidden":
                review_id = _safe_uuid(payload.get("review_id"))
                if review_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(PanditReviewProjection, review_id)
                    if projection:
                        projection.is_visible = False
                        await db.commit()
                continue

            if event_type in {
                "admin.verify_pandit_requested",
                "admin.reject_pandit_requested",
                "admin.suspend_pandit_requested",
                "admin.reinstate_pandit_requested",
            }:
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                if pandit_id is None:
                    continue

                async with AsyncSessionLocal() as db:
                    pandit = await db.get(PanditProfile, pandit_id)
                    if pandit is None:
                        await db.commit()
                        continue

                    emitted_event_type = None
                    if event_type == "admin.verify_pandit_requested":
                        if pandit.verification_status == VerificationStatus.VERIFIED:
                            await db.commit()
                            continue
                        pandit.verification_status = VerificationStatus.VERIFIED
                        pandit.verification_notes = payload.get("notes")
                        pandit.verified_at = _safe_datetime(event.get("occurred_at"))
                        verified_by = _safe_uuid(payload.get("verified_by"))
                        pandit.verified_by_id = verified_by
                        emitted_event_type = "pandit.verified"
                    elif event_type == "admin.reject_pandit_requested":
                        pandit.verification_status = VerificationStatus.REJECTED
                        pandit.verification_notes = str(payload.get("reason") or "Rejected")
                        emitted_event_type = "pandit.rejected"
                    elif event_type == "admin.suspend_pandit_requested":
                        pandit.verification_status = VerificationStatus.SUSPENDED
                        pandit.is_available = False
                        pandit.verification_notes = f"SUSPENDED: {payload.get('reason') or 'No reason provided'}"
                        emitted_event_type = "pandit.suspended"
                    elif event_type == "admin.reinstate_pandit_requested":
                        if pandit.verification_status != VerificationStatus.SUSPENDED:
                            await db.commit()
                            continue
                        pandit.verification_status = VerificationStatus.VERIFIED
                        pandit.is_available = True
                        pandit.verification_notes = None
                        emitted_event_type = "pandit.reinstated"

                    if emitted_event_type:
                        await enqueue_event(
                            db,
                            topic="admin-events",
                            event_type=emitted_event_type,
                            event_key=str(pandit.id),
                            payload={
                                "pandit_id": str(pandit.id),
                                "user_id": str(pandit.user_id),
                                "notes": payload.get("notes"),
                                "reason": payload.get("reason"),
                                "duration_days": payload.get("duration_days"),
                                "verified_by": payload.get("verified_by"),
                                "rejected_by": payload.get("rejected_by"),
                                "suspended_by": payload.get("suspended_by"),
                                "reinstated_by": payload.get("reinstated_by"),
                                "verification_status": pandit.verification_status.value,
                                "is_available": bool(pandit.is_available),
                                "base_fee": float(pandit.base_fee or 0),
                                "pooja_fees": pandit.pooja_fees or {},
                                "city": pandit.city,
                                "state": pandit.state,
                                "documents": pandit.documents or {},
                                "profile_complete": bool(pandit.profile_complete),
                            },
                        )
                    await db.commit()
                continue

            booking_id = _safe_uuid(payload.get("booking_id"))
            if booking_id is None:
                continue

            async with AsyncSessionLocal() as db:
                if event_type == "booking.created":
                    pandit_id = _safe_uuid(payload.get("pandit_id"))
                    user_id = _safe_uuid(payload.get("user_id"))
                    scheduled_at_raw = payload.get("scheduled_at")
                    duration_hrs = payload.get("duration_hrs")
                    if pandit_id is None or user_id is None or scheduled_at_raw is None or duration_hrs is None:
                        await db.commit()
                        continue
                    scheduled_at_value = _safe_datetime(scheduled_at_raw)
                    if scheduled_at_value is None:
                        await db.commit()
                        continue

                    existing = await db.get(PanditBookingProjection, booking_id)
                    if not existing:
                        db.add(
                            PanditBookingProjection(
                                booking_id=booking_id,
                                booking_number=str(payload.get("booking_number") or booking_id),
                                pandit_id=pandit_id,
                                user_id=user_id,
                                scheduled_at=scheduled_at_value,
                                duration_hrs=duration_hrs,
                                status=str(payload.get("status") or "SLOT_LOCKED"),
                                pandit_payout=payload.get("pandit_payout", 0) or 0,
                            )
                        )
                    await db.commit()
                    continue

                projection = await db.get(PanditBookingProjection, booking_id)
                if not projection:
                    await db.commit()
                    continue

                status_map = {
                    "booking.payment_pending": "PAYMENT_PENDING",
                    "booking.awaiting_pandit": "AWAITING_PANDIT",
                    "booking.confirmed": "CONFIRMED",
                    "booking.declined": "DECLINED",
                    "booking.cancelled": "CANCELLED",
                    "booking.completed": "COMPLETED",
                }
                if event_type in status_map:
                    projection.status = status_map[event_type]
                    if event_type == "booking.completed":
                        projection.completed_at = projection.completed_at or projection.updated_at
                elif event_type == "payment.payout_processed":
                    projection.payout_amount = payload.get("amount", 0) or 0
                    payout_at = payload.get("occurred_at") or event.get("occurred_at")
                    if payout_at:
                        projection.payout_at = _safe_datetime(payout_at)
                await db.commit()
    finally:
        await consumer.stop()


async def run_user_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "pandit-events",
        "admin-events",
        "review-events",
        "user-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="user-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type in {"pandit.updated", "pandit.verified", "pandit.rejected", "pandit.suspended", "pandit.reinstated"}:
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                user_id = _safe_uuid(payload.get("user_id"))
                if pandit_id is None or user_id is None:
                    continue

                async with AsyncSessionLocal() as db:
                    projection = await db.get(UserPanditProjection, pandit_id)
                    if projection is None:
                        projection = UserPanditProjection(
                            pandit_id=pandit_id,
                            user_id=user_id,
                        )
                        db.add(projection)
                    projection.user_id = user_id
                    if payload.get("city") is not None:
                        projection.city = payload.get("city")
                    if payload.get("rating_avg") is not None:
                        projection.rating_avg = payload.get("rating_avg")
                    if payload.get("rating_count") is not None:
                        projection.rating_count = payload.get("rating_count")
                    if payload.get("base_fee") is not None:
                        projection.base_fee = payload.get("base_fee")
                    if payload.get("verification_status") is not None:
                        projection.verification_status = str(payload.get("verification_status"))
                    elif event_type == "pandit.verified":
                        projection.verification_status = "VERIFIED"
                    elif event_type == "pandit.rejected":
                        projection.verification_status = "REJECTED"
                    elif event_type == "pandit.suspended":
                        projection.verification_status = "SUSPENDED"
                    elif event_type == "pandit.reinstated":
                        projection.verification_status = "VERIFIED"

                    if payload.get("is_available") is not None:
                        projection.is_available = bool(payload.get("is_available"))
                    elif event_type in {"pandit.rejected", "pandit.suspended"}:
                        projection.is_available = False
                    elif event_type == "pandit.reinstated":
                        projection.is_available = True
                    await db.commit()
                continue

            if event_type in {"user.upserted", "user.updated"}:
                user_id = _safe_uuid(payload.get("user_id"))
                if user_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    account_projection = await db.get(UserAccountProjection, user_id)
                    if account_projection is None:
                        account_projection = UserAccountProjection(
                            user_id=user_id,
                            email=str(payload.get("email") or f"{user_id}@user.local"),
                            name=str(payload.get("name") or "User"),
                            role=str(payload.get("role") or UserRole.USER.value),
                            preferred_language=str(payload.get("preferred_language") or "hi"),
                            is_active=bool(payload.get("is_active", True)),
                        )
                        db.add(account_projection)
                    if payload.get("email") is not None:
                        account_projection.email = str(payload.get("email"))
                    if payload.get("name") is not None:
                        account_projection.name = str(payload.get("name"))
                    if payload.get("phone") is not None:
                        account_projection.phone = payload.get("phone")
                    if payload.get("avatar_url") is not None:
                        account_projection.avatar_url = payload.get("avatar_url")
                    if payload.get("role") is not None:
                        account_projection.role = str(payload.get("role"))
                    if payload.get("preferred_language") is not None:
                        account_projection.preferred_language = str(payload.get("preferred_language"))
                    if payload.get("fcm_token") is not None:
                        account_projection.fcm_token = payload.get("fcm_token")
                    if payload.get("is_active") is not None:
                        account_projection.is_active = bool(payload.get("is_active"))

                    result = await db.execute(
                        select(UserPanditProjection).where(UserPanditProjection.user_id == user_id)
                    )
                    for projection in result.scalars():
                        if payload.get("name") is not None:
                            projection.name = payload.get("name")
                        if payload.get("avatar_url") is not None:
                            projection.avatar_url = payload.get("avatar_url")
                    await db.commit()
                continue

            if event_type in {"review.submitted", "review.hidden"}:
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                if pandit_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(UserPanditProjection, pandit_id)
                    if projection:
                        if payload.get("pandit_rating_avg") is not None:
                            projection.rating_avg = payload.get("pandit_rating_avg")
                        if payload.get("pandit_rating_count") is not None:
                            projection.rating_count = payload.get("pandit_rating_count")
                        await db.commit()
    finally:
        await consumer.stop()


async def run_auth_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "admin-commands",
        "user-commands",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="auth-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type not in {
                "admin.suspend_user_requested",
                "admin.reactivate_user_requested",
                "user.profile_update_requested",
            }:
                continue

            user_id = _safe_uuid(payload.get("user_id"))
            if user_id is None:
                continue

            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
                if user is None:
                    await db.commit()
                    continue

                emitted_event_type = None
                if event_type == "admin.suspend_user_requested":
                    if not user.is_active:
                        await db.commit()
                        continue
                    user.is_active = False
                    emitted_event_type = "user.suspended"
                elif event_type == "admin.reactivate_user_requested":
                    if user.is_active:
                        await db.commit()
                        continue
                    user.is_active = True
                    emitted_event_type = "user.reactivated"
                elif event_type == "user.profile_update_requested":
                    updates = payload.get("updates") or {}
                    phone = updates.get("phone")
                    if phone:
                        existing = await db.execute(
                            select(User).where(User.phone == phone, User.id != user.id)
                        )
                        if existing.scalar_one_or_none():
                            await db.commit()
                            continue
                    for field in ("name", "phone", "preferred_language", "fcm_token"):
                        if updates.get(field) is not None:
                            setattr(user, field, updates.get(field))

                if emitted_event_type:
                    await enqueue_event(
                        db,
                        topic="admin-events",
                        event_type=emitted_event_type,
                        event_key=str(user.id),
                        payload={
                            "user_id": str(user.id),
                            "reason": payload.get("reason"),
                            "suspended_by": payload.get("suspended_by"),
                            "reactivated_by": payload.get("reactivated_by"),
                            "is_active": bool(user.is_active),
                        },
                    )
                if emitted_event_type or event_type == "user.profile_update_requested":
                    await enqueue_event(
                        db,
                        topic="user-events",
                        event_type="user.updated",
                        event_key=str(user.id),
                        payload={
                            "user_id": str(user.id),
                            "name": user.name,
                            "email": user.email,
                            "phone": user.phone,
                            "preferred_language": user.preferred_language,
                            "avatar_url": user.avatar_url,
                            "fcm_token": user.fcm_token,
                            "role": user.role.value,
                            "is_active": user.is_active,
                        },
                    )
                await db.commit()
    finally:
        await consumer.stop()


async def run_admin_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "pandit-events",
        "admin-events",
        "user-events",
        "booking-events",
        "payment-events",
        "review-events",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="admin-service",
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value.decode("utf-8"))
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type in {"pandit.updated", "pandit.verified", "pandit.rejected", "pandit.suspended", "pandit.reinstated"}:
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                user_id = _safe_uuid(payload.get("user_id"))
                if pandit_id is None or user_id is None:
                    continue

                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminPanditReviewProjection, pandit_id)
                    if projection is None:
                        projection = AdminPanditReviewProjection(
                            pandit_id=pandit_id,
                            user_id=user_id,
                        )
                        db.add(projection)

                    projection.user_id = user_id
                    if payload.get("city") is not None:
                        projection.city = payload.get("city")
                    if payload.get("state") is not None:
                        projection.state = payload.get("state")
                    if payload.get("experience_years") is not None:
                        projection.experience_years = int(payload.get("experience_years") or 0)
                    if payload.get("languages") is not None:
                        projection.languages = list(payload.get("languages") or [])
                    if payload.get("poojas_offered") is not None:
                        projection.poojas_offered = [str(item) for item in (payload.get("poojas_offered") or [])]
                    if payload.get("bio") is not None:
                        projection.bio = payload.get("bio")
                    if payload.get("documents") is not None:
                        projection.documents = payload.get("documents")
                    if payload.get("verification_status") is not None:
                        projection.verification_status = str(payload.get("verification_status"))
                    if payload.get("is_available") is not None:
                        projection.is_available = bool(payload.get("is_available"))
                    if payload.get("profile_complete") is not None:
                        projection.profile_complete = bool(payload.get("profile_complete"))
                    await db.commit()
                continue

            if event_type in {"user.upserted", "user.updated"}:
                user_id = _safe_uuid(payload.get("user_id"))
                if user_id is None:
                    continue

                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminUserProjection, user_id)
                    if projection is None:
                        projection = AdminUserProjection(user_id=user_id)
                        db.add(projection)
                    if payload.get("name") is not None:
                        projection.name = payload.get("name")
                    if payload.get("email") is not None:
                        projection.email = payload.get("email")
                    if payload.get("phone") is not None:
                        projection.phone = payload.get("phone")
                    if payload.get("role") is not None:
                        projection.role = str(payload.get("role"))
                    if payload.get("is_active") is not None:
                        projection.is_active = bool(payload.get("is_active"))

                    result = await db.execute(
                        select(AdminPanditReviewProjection).where(AdminPanditReviewProjection.user_id == user_id)
                    )
                    for projection in result.scalars():
                        if payload.get("name") is not None:
                            projection.name = payload.get("name")
                        if payload.get("email") is not None:
                            projection.email = payload.get("email")
                        if payload.get("phone") is not None:
                            projection.phone = payload.get("phone")
                    await db.commit()
                continue

            if event_type in {"user.suspended", "user.reactivated"}:
                user_id = _safe_uuid(payload.get("user_id"))
                if user_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminUserProjection, user_id)
                    if projection is None:
                        projection = AdminUserProjection(user_id=user_id)
                        db.add(projection)
                    if payload.get("is_active") is not None:
                        projection.is_active = bool(payload.get("is_active"))
                    await db.commit()
                continue

            if event_type == "booking.created":
                booking_id = _safe_uuid(payload.get("booking_id"))
                if booking_id is None:
                    continue
                user_id = _safe_uuid(payload.get("user_id"))
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                pooja_id = _safe_uuid(payload.get("pooja_id"))
                scheduled_at = _safe_datetime(payload.get("scheduled_at"))
                if user_id is None or pandit_id is None or pooja_id is None or scheduled_at is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminBookingProjection, booking_id)
                    if projection is None:
                        projection = AdminBookingProjection(
                            booking_id=booking_id,
                            booking_number=str(payload.get("booking_number") or booking_id),
                            user_id=user_id,
                            pandit_id=pandit_id,
                            pooja_id=pooja_id,
                            status=str(payload.get("status") or "DRAFT"),
                            scheduled_at=scheduled_at,
                            total_amount=payload.get("total_amount") or 0,
                            platform_fee=payload.get("platform_fee") or 0,
                            pandit_payout=payload.get("pandit_payout") or 0,
                        )
                        db.add(projection)
                    await db.commit()
                continue

            if event_type in {"booking.confirmed", "booking.declined", "booking.completed", "booking.cancelled"}:
                booking_id = _safe_uuid(payload.get("booking_id"))
                if booking_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminBookingProjection, booking_id)
                    if projection:
                        if payload.get("status") is not None:
                            projection.status = str(payload.get("status"))
                        elif event_type == "booking.confirmed":
                            projection.status = "CONFIRMED"
                        elif event_type == "booking.declined":
                            projection.status = "DECLINED"
                        elif event_type == "booking.completed":
                            projection.status = "COMPLETED"
                        elif event_type == "booking.cancelled":
                            projection.status = "CANCELLED"
                        if payload.get("reason") is not None:
                            projection.cancellation_reason = str(payload.get("reason"))
                        if payload.get("cancellation_reason") is not None:
                            projection.cancellation_reason = str(payload.get("cancellation_reason"))
                        await db.commit()
                continue

            if event_type in {"payment.initiated", "payment.captured", "payment.failed", "payment.refunded"}:
                payment_id = _safe_uuid(payload.get("payment_id"))
                booking_id = _safe_uuid(payload.get("booking_id"))
                if payment_id is None or booking_id is None:
                    continue
                user_id = _safe_uuid(payload.get("user_id"))
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                occurred_at = _safe_datetime(event.get("occurred_at"))
                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminPaymentProjection, payment_id)
                    if projection is None:
                        projection = AdminPaymentProjection(
                            payment_id=payment_id,
                            booking_id=booking_id,
                            user_id=user_id,
                            pandit_id=pandit_id,
                            amount=payload.get("amount") or 0,
                            status="PENDING",
                        )
                        db.add(projection)
                    if user_id is not None:
                        projection.user_id = user_id
                    if pandit_id is not None:
                        projection.pandit_id = pandit_id
                    if payload.get("amount") is not None:
                        projection.amount = payload.get("amount")
                    if event_type == "payment.initiated":
                        projection.status = "PENDING"
                    elif event_type == "payment.captured":
                        projection.status = "CAPTURED"
                        projection.captured_at = occurred_at
                    elif event_type == "payment.failed":
                        projection.status = "FAILED"
                    elif event_type == "payment.refunded":
                        projection.status = "REFUNDED"
                        projection.refunded_at = occurred_at
                    await db.commit()
                continue

            if event_type == "review.submitted":
                review_id = _safe_uuid(payload.get("review_id"))
                pandit_id = _safe_uuid(payload.get("pandit_id"))
                if review_id is None or pandit_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminReviewProjection, review_id)
                    if projection is None:
                        projection = AdminReviewProjection(
                            review_id=review_id,
                            pandit_id=pandit_id,
                            rating=int(payload.get("rating") or 0),
                            is_visible=True,
                        )
                        db.add(projection)
                    else:
                        projection.rating = int(payload.get("rating") or projection.rating)
                        projection.is_visible = True
                    await db.commit()
                continue

            if event_type == "review.hidden":
                review_id = _safe_uuid(payload.get("review_id"))
                if review_id is None:
                    continue
                async with AsyncSessionLocal() as db:
                    projection = await db.get(AdminReviewProjection, review_id)
                    if projection:
                        projection.is_visible = False
                        await db.commit()
                continue
    finally:
        await consumer.stop()


def consumer_factory_for_service(service_name: str):
    if service_name == "auth-service":
        return run_auth_consumer
    if service_name == "booking-service":
        return run_booking_consumer
    if service_name == "payment-service":
        return run_payment_consumer
    if service_name == "notification-service":
        return run_notification_consumer
    if service_name == "review-service":
        return run_review_consumer
    if service_name == "search-service":
        return run_search_consumer
    if service_name == "pandit-service":
        return run_pandit_consumer
    if service_name == "user-service":
        return run_user_consumer
    if service_name == "admin-service":
        return run_admin_consumer
    return None


async def start_service_consumer(service_name: str) -> asyncio.Task | None:
    if not settings.EVENT_BUS_ENABLED:
        return None

    factory = consumer_factory_for_service(service_name)
    if not factory:
        return None

    async def _run_forever() -> None:
        while True:
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Consumer for %s crashed and will restart: %s", service_name, exc)
                await asyncio.sleep(5)

    task = asyncio.create_task(_run_forever(), name=f"{service_name}-event-consumer")
    logger.info("Started consumer task for %s", service_name)
    return task
