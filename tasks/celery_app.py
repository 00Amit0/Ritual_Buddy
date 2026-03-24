"""
tasks/celery_app.py
Celery application instance — shared across all task modules.

Workers are started with:
    celery -A tasks.celery_app worker --loglevel=info --concurrency=4

Beat scheduler (periodic tasks):
    celery -A tasks.celery_app beat --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab
from config.settings import settings

celery_app = Celery(
    "pandit_booking",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "tasks.notification_tasks",
        "tasks.payment_tasks",
    ],
)

# ── Configuration ─────────────────────────────────────────────────────────────

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,

    # Reliability: acknowledge task AFTER execution, not before
    # This prevents task loss if worker dies mid-execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Result expiry: keep task results for 1 hour
    result_expires=3600,

    # Retry: max 3 retries with exponential backoff
    task_max_retries=3,

    # Rate limits (per worker per second)
    task_annotations={
        "tasks.notification_tasks.send_push_notification": {"rate_limit": "30/s"},
        "tasks.notification_tasks.send_sms": {"rate_limit": "10/s"},
        "tasks.notification_tasks.send_email": {"rate_limit": "20/s"},
    },

    # Routing: separate queues for different priority levels
    task_routes={
        "tasks.notification_tasks.*": {"queue": "notifications"},
        "tasks.payment_tasks.process_single_payout": {"queue": "payments"},
        "tasks.payment_tasks.process_pending_payouts": {"queue": "payments"},
        "tasks.payment_tasks.process_refund": {"queue": "payments"},
        "tasks.payment_tasks.release_expired_slot_locks": {"queue": "default"},
    },

    # Worker prefetch: 1 task at a time for long-running tasks
    worker_prefetch_multiplier=1,
    # Ensure broker reconnect behavior is explicit at startup
    broker_connection_retry_on_startup=True,
)

# ── Periodic Tasks (Beat Schedule) ────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    # Release Redis slot locks for bookings stuck in SLOT_LOCKED/PAYMENT_PENDING
    # Runs every 5 minutes
    "release-expired-slot-locks": {
        "task": "tasks.payment_tasks.release_expired_slot_locks",
        "schedule": 300,  # every 5 minutes
    },

    "send-booking-reminders": {
        "task": "tasks.notification_tasks.send_booking_reminders",
        "schedule": crontab(minute=0),
    },

    "process-nightly-payouts": {
        "task": "tasks.payment_tasks.process_pending_payouts",
        "schedule": crontab(hour=20, minute=30),
    },

    "send-review-requests": {
        "task": "tasks.notification_tasks.send_review_requests",
        "schedule": crontab(minute=0),
    },
}
