"""
tasks/notification_tasks.py
Celery tasks for notification delivery and notification-local scheduled workflows.
"""

import logging
from datetime import datetime, timedelta, timezone

from celery import Task
from sqlalchemy import select

from shared.utils.third_party import (
    send_email as send_email_channel,
    send_fcm_push as send_fcm_push_channel,
    send_sms as send_sms_channel,
)
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Base class that provides a synchronous DB session for tasks."""

    abstract = True

    def get_session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from config.settings import settings

        sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url, pool_pre_ping=True)
        session_factory = sessionmaker(bind=engine)
        return session_factory()


def _send_fcm(fcm_token: str, title: str, body: str, data: dict | None = None) -> bool:
    return send_fcm_push_channel(fcm_token, title, body, data)


def _send_sms(phone: str, body: str) -> bool:
    return send_sms_channel(phone, body)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    return send_email_channel(to_email, "User", subject, html_body)


TEMPLATES = {
    "BOOKING_REMINDER": {
        "push_title": "Reminder: Pooja Tomorrow",
        "push_body": "Your booking {booking_number} is scheduled for tomorrow at {time}.",
        "sms": "Reminder: Your pandit is scheduled for tomorrow. Booking {booking_number}.",
    },
    "REVIEW_REQUEST": {
        "push_title": "How was your experience?",
        "push_body": "Please rate your pandit for booking {booking_number}.",
    },
}


def _render(template: str, **kwargs) -> str:
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3, default_retry_delay=60)
def send_push_notification(self, fcm_token: str, title: str, body: str, data: dict | None = None):
    success = _send_fcm(fcm_token, title, body, data)
    if not success:
        raise self.retry(countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3, default_retry_delay=120)
def send_sms(self, phone: str, body: str):
    success = _send_sms(phone, body)
    if not success:
        raise self.retry(countdown=120 * (2 ** self.request.retries))


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3, default_retry_delay=60)
def send_email(self, to_email: str, subject: str, html_body: str):
    success = _send_email(to_email, subject, html_body)
    if not success:
        raise self.retry(countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3)
def send_booking_confirmed(self, booking_id: str):
    logger.warning("send_booking_confirmed(%s) is deprecated; use booking-events instead", booking_id)


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3)
def send_new_booking_request(self, booking_id: str):
    logger.warning("send_new_booking_request(%s) is deprecated; use booking-events instead", booking_id)


@celery_app.task(bind=True, base=DatabaseTask, max_retries=3)
def send_booking_declined(self, booking_id: str):
    logger.warning("send_booking_declined(%s) is deprecated; use booking-events instead", booking_id)


@celery_app.task
def send_booking_reminders():
    from shared.models.models import NotificationBookingProjection, NotificationRecord, NotificationType, User

    db = DatabaseTask().get_session()
    try:
        now = datetime.now(timezone.utc)
        window_start = now + timedelta(hours=24)
        window_end = now + timedelta(hours=25)

        bookings = db.execute(
            select(NotificationBookingProjection).where(
                NotificationBookingProjection.status == "CONFIRMED",
                NotificationBookingProjection.scheduled_at >= window_start,
                NotificationBookingProjection.scheduled_at < window_end,
                NotificationBookingProjection.reminder_sent_at == None,  # noqa: E711
            )
        ).scalars().all()

        for booking in bookings:
            user = db.execute(select(User).where(User.id == booking.user_id)).scalar_one_or_none()
            if not user:
                continue

            template = TEMPLATES["BOOKING_REMINDER"]
            scheduled_time = booking.scheduled_at.strftime("%I:%M %p")
            vars = {"booking_number": booking.booking_number, "time": scheduled_time}

            db.add(
                NotificationRecord(
                    user_id=user.id,
                    type=NotificationType.BOOKING_REMINDER,
                    title=template["push_title"],
                    body=_render(template["push_body"], **vars),
                    booking_id=booking.booking_id,
                )
            )
            booking.reminder_sent_at = now

            if user.fcm_token:
                send_push_notification.delay(
                    user.fcm_token,
                    template["push_title"],
                    _render(template["push_body"], **vars),
                )
            if user.phone:
                send_sms.delay(user.phone, _render(template["sms"], **vars))

        db.commit()
        logger.info("Sent %s booking reminders", len(bookings))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("send_booking_reminders failed: %s", exc)
    finally:
        db.close()


@celery_app.task
def send_review_requests():
    from shared.models.models import NotificationBookingProjection, NotificationRecord, NotificationType, User

    db = DatabaseTask().get_session()
    try:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=3)
        window_end = now - timedelta(hours=2)

        bookings = db.execute(
            select(NotificationBookingProjection).where(
                NotificationBookingProjection.status == "COMPLETED",
                NotificationBookingProjection.completed_at >= window_start,
                NotificationBookingProjection.completed_at < window_end,
                NotificationBookingProjection.review_request_sent_at == None,  # noqa: E711
                NotificationBookingProjection.review_submitted_at == None,  # noqa: E711
            )
        ).scalars().all()

        sent = 0
        for booking in bookings:
            user = db.execute(select(User).where(User.id == booking.user_id)).scalar_one_or_none()
            if not user or not user.fcm_token:
                continue

            template = TEMPLATES["REVIEW_REQUEST"]
            vars = {"booking_number": booking.booking_number}

            db.add(
                NotificationRecord(
                    user_id=user.id,
                    type=NotificationType.REVIEW_REQUEST,
                    title=template["push_title"],
                    body=_render(template["push_body"], **vars),
                    booking_id=booking.booking_id,
                )
            )
            booking.review_request_sent_at = now
            send_push_notification.delay(
                user.fcm_token,
                template["push_title"],
                _render(template["push_body"], **vars),
                {"booking_id": str(booking.booking_id), "type": "REVIEW_REQUEST"},
            )
            sent += 1

        db.commit()
        logger.info("Sent %s review requests", sent)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("send_review_requests failed: %s", exc)
    finally:
        db.close()
