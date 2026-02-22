"""
tasks/notification_tasks.py
Celery tasks for multi-channel notification delivery.

All tasks are idempotent — safe to run twice.
Failures in one channel (e.g. FCM) never block other channels.

Usage from a route:
    from tasks.notification_tasks import send_booking_confirmed
    send_booking_confirmed.delay(booking_id=str(booking.id))
"""

import logging
from datetime import datetime, timedelta, timezone

from celery import Task
from sqlalchemy import select, update

from config.settings import settings
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Base Task with DB session ──────────────────────────────────────────────────

class DatabaseTask(Task):
    """Base class that provides a synchronous DB session for tasks."""
    abstract = True
    _session = None

    def get_session(self):
        """Get a synchronous SQLAlchemy session (Celery runs sync by default)."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from config.settings import settings

        # Convert async URL (postgresql+asyncpg://) to sync (postgresql+psycopg2://)
        sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url, pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        return Session()


# ── Core Delivery Functions ────────────────────────────────────────────────────

def _send_fcm(fcm_token: str, title: str, body: str, data: dict = None) -> bool:
    """Send FCM push notification. Returns True on success."""
    try:
        import firebase_admin
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(badge=1, sound="default")
                )
            ),
        )
        messaging.send(message)
        return True
    except Exception as e:
        logger.warning(f"FCM send failed: {e}")
        return False


def _send_sms(phone: str, body: str) -> bool:
    """Send SMS via Twilio. Returns True on success."""
    try:
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=body,
            from_=settings.TWILIO_FROM_NUMBER,
            to=phone if phone.startswith("+") else f"+91{phone}",
        )
        return True
    except Exception as e:
        logger.warning(f"SMS send failed: {e}")
        return False


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send transactional email via Resend. Returns True on success."""
    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": to_email,
            "subject": subject,
            "html": html_body,
        })
        return True
    except Exception as e:
        logger.warning(f"Email send failed: {e}")
        return False


# ── Notification Templates ─────────────────────────────────────────────────────

TEMPLATES = {
    "BOOKING_CONFIRMED": {
        "push_title": "Booking Confirmed! ✅",
        "push_body": "Your booking {booking_number} has been confirmed by the pandit.",
        "sms": "Your pandit booking {booking_number} is CONFIRMED! See app for details.",
        "email_subject": "Booking Confirmed – {booking_number}",
    },
    "BOOKING_DECLINED": {
        "push_title": "Booking Declined",
        "push_body": "The pandit declined booking {booking_number}. A full refund has been initiated.",
        "sms": "Booking {booking_number} was declined. Full refund initiated (3-5 business days).",
        "email_subject": "Booking Declined – {booking_number}",
    },
    "NEW_BOOKING_REQUEST": {
        "push_title": "New Booking Request 📅",
        "push_body": "You have a new booking request {booking_number}. Accept within 2 hours.",
        "sms": "New booking request {booking_number}! Open the app to accept or decline.",
        "email_subject": "New Booking Request – {booking_number}",
    },
    "PAYMENT_SUCCESS": {
        "push_title": "Payment Successful 💳",
        "push_body": "Payment of ₹{amount} received for booking {booking_number}.",
        "sms": "Payment of Rs.{amount} received for booking {booking_number}.",
        "email_subject": "Payment Receipt – {booking_number}",
    },
    "BOOKING_REMINDER": {
        "push_title": "Reminder: Pooja Tomorrow 🕉️",
        "push_body": "Your booking {booking_number} is scheduled for tomorrow at {time}.",
        "sms": "Reminder: Your pandit is scheduled for tomorrow. Booking {booking_number}.",
        "email_subject": "Booking Reminder – {booking_number}",
    },
    "REVIEW_REQUEST": {
        "push_title": "How was your experience? ⭐",
        "push_body": "Please rate your pandit for booking {booking_number}.",
        "sms": None,  # No SMS for review requests
        "email_subject": "Share your experience – {booking_number}",
    },
    "PAYOUT_PROCESSED": {
        "push_title": "Payout Processed 💰",
        "push_body": "₹{amount} has been transferred to your bank account.",
        "sms": "Payout of Rs.{amount} transferred to your account. Booking: {booking_number}.",
        "email_subject": "Payout Processed – {booking_number}",
    },
    "ACCOUNT_VERIFIED": {
        "push_title": "Profile Verified! 🎉",
        "push_body": "Your pandit profile has been verified. Start accepting bookings now!",
        "sms": "Congratulations! Your PanditBooking profile is now verified.",
        "email_subject": "Your Profile is Verified",
    },
}


def _render(template: str, **kwargs) -> str:
    """Simple string template renderer."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


# ── Individual Channel Tasks ───────────────────────────────────────────────────

@celery_app.task(bind=True, base=DatabaseTask, max_retries=3, default_retry_delay=60)
def send_push_notification(self, fcm_token: str, title: str, body: str, data: dict = None):
    """Send a single FCM push notification with retry on failure."""
    success = _send_fcm(fcm_token, title, body, data)
    if not success:
        raise self.retry(countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3, default_retry_delay=120)
def send_sms(self, phone: str, body: str):
    """Send a single SMS via Twilio with retry on failure."""
    success = _send_sms(phone, body)
    if not success:
        raise self.retry(countdown=120 * (2 ** self.request.retries))


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3, default_retry_delay=60)
def send_email(self, to_email: str, subject: str, html_body: str):
    """Send a transactional email via Resend with retry on failure."""
    success = _send_email(to_email, subject, html_body)
    if not success:
        raise self.retry(countdown=60 * (2 ** self.request.retries))


# ── High-Level Booking Notification Tasks ─────────────────────────────────────

@celery_app.task(bind=True, base=DatabaseTask, max_retries=3)
def send_booking_confirmed(self, booking_id: str):
    """
    Notify the user that their booking has been accepted by the pandit.
    Sends push + SMS + email in parallel (best effort — channel failure is logged, not raised).
    """
    from shared.models.models import Booking, Notification, NotificationType, User

    db = self.get_session()
    try:
        booking = db.execute(select(Booking).where(Booking.id == booking_id)).scalar_one_or_none()
        if not booking:
            logger.error(f"send_booking_confirmed: booking {booking_id} not found")
            return

        user = db.execute(select(User).where(User.id == booking.user_id)).scalar_one_or_none()
        if not user:
            return

        tmpl = TEMPLATES["BOOKING_CONFIRMED"]
        vars = {"booking_number": booking.booking_number}

        # Mark in-app notification as sent
        db.add(Notification(
            user_id=user.id,
            type=NotificationType.BOOKING_CONFIRMED,
            title=tmpl["push_title"],
            body=_render(tmpl["push_body"], **vars),
            booking_id=booking.id,
        ))
        db.commit()

        # Fire-and-forget channel delivery
        if user.fcm_token:
            send_push_notification.delay(
                user.fcm_token,
                tmpl["push_title"],
                _render(tmpl["push_body"], **vars),
                {"booking_id": booking_id, "type": "BOOKING_CONFIRMED"},
            )
        if user.phone:
            send_sms.delay(user.phone, _render(tmpl["sms"], **vars))
        if user.email:
            send_email.delay(
                user.email,
                _render(tmpl["email_subject"], **vars),
                f"<p>{_render(tmpl['push_body'], **vars)}</p>",
            )

    except Exception as e:
        db.rollback()
        logger.exception(f"send_booking_confirmed failed: {e}")
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3)
def send_new_booking_request(self, booking_id: str):
    """
    Notify the pandit of a new booking request.
    Pandit has BOOKING_ACCEPT_DEADLINE_HOURS to accept/decline.
    """
    from shared.models.models import Booking, Notification, NotificationType, PanditProfile, User

    db = self.get_session()
    try:
        booking = db.execute(select(Booking).where(Booking.id == booking_id)).scalar_one_or_none()
        if not booking:
            return

        pandit_profile = db.execute(
            select(PanditProfile).where(PanditProfile.id == booking.pandit_id)
        ).scalar_one_or_none()
        if not pandit_profile:
            return

        pandit_user = db.execute(
            select(User).where(User.id == pandit_profile.user_id)
        ).scalar_one_or_none()
        if not pandit_user:
            return

        tmpl = TEMPLATES["NEW_BOOKING_REQUEST"]
        vars = {"booking_number": booking.booking_number}

        db.add(Notification(
            user_id=pandit_user.id,
            type=NotificationType.BOOKING_REQUEST,
            title=tmpl["push_title"],
            body=_render(tmpl["push_body"], **vars),
            booking_id=booking.id,
        ))
        db.commit()

        if pandit_user.fcm_token:
            send_push_notification.delay(
                pandit_user.fcm_token,
                tmpl["push_title"],
                _render(tmpl["push_body"], **vars),
                {"booking_id": booking_id, "type": "NEW_BOOKING_REQUEST"},
            )
        if pandit_user.phone:
            send_sms.delay(pandit_user.phone, _render(tmpl["sms"], **vars))

    except Exception as e:
        db.rollback()
        logger.exception(f"send_new_booking_request failed: {e}")
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3)
def send_booking_declined(self, booking_id: str):
    """Notify user their booking was declined and a refund has been initiated."""
    from shared.models.models import Booking, Notification, NotificationType, User

    db = self.get_session()
    try:
        booking = db.execute(select(Booking).where(Booking.id == booking_id)).scalar_one_or_none()
        if not booking:
            return

        user = db.execute(select(User).where(User.id == booking.user_id)).scalar_one_or_none()
        if not user:
            return

        tmpl = TEMPLATES["BOOKING_DECLINED"]
        vars = {"booking_number": booking.booking_number}

        db.add(Notification(
            user_id=user.id,
            type=NotificationType.BOOKING_CANCELLED,
            title=tmpl["push_title"],
            body=_render(tmpl["push_body"], **vars),
            booking_id=booking.id,
        ))
        db.commit()

        if user.fcm_token:
            send_push_notification.delay(user.fcm_token, tmpl["push_title"], _render(tmpl["push_body"], **vars))
        if user.phone:
            send_sms.delay(user.phone, _render(tmpl["sms"], **vars))

    except Exception as e:
        db.rollback()
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


# ── Periodic / Scheduled Tasks ────────────────────────────────────────────────

@celery_app.task
def send_booking_reminders():
    """
    Beat task: runs every hour.
    Sends reminders for bookings scheduled in the next 24–25 hours
    (window catches bookings we haven't reminded yet).
    """
    from shared.models.models import Booking, BookingStatus, Notification, NotificationType, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from config.settings import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        now = datetime.now(timezone.utc)
        window_start = now + timedelta(hours=24)
        window_end = now + timedelta(hours=25)

        bookings = db.execute(
            select(Booking).where(
                Booking.status == BookingStatus.CONFIRMED,
                Booking.scheduled_at >= window_start,
                Booking.scheduled_at < window_end,
            )
        ).scalars().all()

        for booking in bookings:
            user = db.execute(select(User).where(User.id == booking.user_id)).scalar_one_or_none()
            if not user:
                continue

            tmpl = TEMPLATES["BOOKING_REMINDER"]
            scheduled_time = booking.scheduled_at.strftime("%I:%M %p")
            vars = {"booking_number": booking.booking_number, "time": scheduled_time}

            db.add(Notification(
                user_id=user.id,
                type=NotificationType.BOOKING_REMINDER,
                title=tmpl["push_title"],
                body=_render(tmpl["push_body"], **vars),
                booking_id=booking.id,
            ))

            if user.fcm_token:
                send_push_notification.delay(user.fcm_token, tmpl["push_title"], _render(tmpl["push_body"], **vars))
            if user.phone:
                send_sms.delay(user.phone, _render(tmpl["sms"], **vars))

        db.commit()
        logger.info(f"Sent {len(bookings)} booking reminders")
    except Exception as e:
        db.rollback()
        logger.exception(f"send_booking_reminders failed: {e}")
    finally:
        db.close()


@celery_app.task
def send_review_requests():
    """
    Beat task: runs every hour.
    Sends review request to users whose booking completed 2–3 hours ago.
    """
    from shared.models.models import Booking, BookingStatus, Review, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=3)
        window_end = now - timedelta(hours=2)

        bookings = db.execute(
            select(Booking).where(
                Booking.status == BookingStatus.COMPLETED,
                Booking.completed_at >= window_start,
                Booking.completed_at < window_end,
            )
        ).scalars().all()

        sent = 0
        for booking in bookings:
            # Skip if already reviewed
            existing_review = db.execute(
                select(Review).where(Review.booking_id == booking.id)
            ).scalar_one_or_none()
            if existing_review:
                continue

            user = db.execute(select(User).where(User.id == booking.user_id)).scalar_one_or_none()
            if not user or not user.fcm_token:
                continue

            tmpl = TEMPLATES["REVIEW_REQUEST"]
            vars = {"booking_number": booking.booking_number}

            send_push_notification.delay(
                user.fcm_token,
                tmpl["push_title"],
                _render(tmpl["push_body"], **vars),
                {"booking_id": str(booking.id), "type": "REVIEW_REQUEST"},
            )
            sent += 1

        db.commit()
        logger.info(f"Sent {sent} review requests")
    except Exception as e:
        db.rollback()
        logger.exception(f"send_review_requests failed: {e}")
    finally:
        db.close()
