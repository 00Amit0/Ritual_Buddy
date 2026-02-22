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
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
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
        "tasks.payment_tasks.process_payout": {"queue": "payments"},
        "tasks.payment_tasks.release_expired_slot_locks": {"queue": "default"},
    },

    # Worker prefetch: 1 task at a time for long-running tasks
    worker_prefetch_multiplier=1,
)

# ── Periodic Tasks (Beat Schedule) ────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    # Release Redis slot locks for bookings stuck in SLOT_LOCKED/PAYMENT_PENDING
    # Runs every 5 minutes
    "release-expired-slot-locks": {
        "task": "tasks.payment_tasks.release_expired_slot_locks",
        "schedule": 300,  # every 5 minutes
    },

    # Send booking reminders 24 hours before scheduled pooja
    # Runs every hour to catch newly created bookings
    "send-booking-reminders": {
        "task": "tasks.notification_tasks.send_booking_reminders",
        "schedule": crontab(minute=0),  # top of every hour
    },

    # Process pending payouts for COMPLETED bookings (escrow release)
    # Runs nightly at 2 AM IST
    "process-nightly-payouts": {
        "task": "tasks.payment_tasks.process_pending_payouts",
        "schedule": crontab(hour=20, minute=30),  # 02:00 IST = 20:30 UTC
    },

    # Send review request to users 2 hours after booking completion
    "send-review-requests": {
        "task": "tasks.notification_tasks.send_review_requests",
        "schedule": crontab(minute=0),  # every hour
    },
}
