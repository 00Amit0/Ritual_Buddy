"""
services/notification/router.py
Notification delivery: FCM push, Twilio SMS, Resend email.
In-app notifications via WebSocket.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.settings import settings
from shared.middleware.auth import get_current_user
from shared.models.models import Notification, User
from shared.schemas.schemas import MessageResponse, NotificationResponse

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ── Notification Senders ──────────────────────────────────────

async def send_fcm_push(fcm_token: str, title: str, body: str, data: dict = None) -> bool:
    """Send Firebase Cloud Messaging push notification."""
    try:
        import firebase_admin
        from firebase_admin import messaging

        if not firebase_admin._apps:
            import firebase_admin.credentials as credentials
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                    sound="default",
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1)
                )
            ),
        )
        messaging.send(message)
        return True
    except Exception as e:
        # Log but don't fail — notification is non-critical
        print(f"FCM push failed: {e}")
        return False


async def send_sms(phone_number: str, message: str) -> bool:
    """Send SMS via Twilio."""
    try:
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=message,
            from_=settings.TWILIO_FROM_NUMBER,
            to=phone_number,
        )
        return True
    except Exception as e:
        print(f"SMS failed: {e}")
        return False


async def send_email(to_email: str, to_name: str, subject: str, html_body: str) -> bool:
    """Send transactional email via Resend."""
    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
            "to": [f"{to_name} <{to_email}>"],
            "subject": subject,
            "html": html_body,
        })
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


# ── Notification Templates ────────────────────────────────────

TEMPLATES = {
    "BOOKING_CONFIRMED": {
        "title": "Booking Confirmed! 🎉",
        "body": "Your booking #{booking_number} has been confirmed. The Pandit will arrive on {scheduled_date}.",
        "sms": "PanditBooking: Your booking #{booking_number} is confirmed for {scheduled_date}. Booking Ref: {booking_number}",
    },
    "BOOKING_DECLINED": {
        "title": "Booking Declined",
        "body": "The Pandit has declined booking #{booking_number}. A full refund will be processed in 3-5 business days.",
        "sms": "PanditBooking: Booking #{booking_number} was declined. Refund initiated.",
    },
    "BOOKING_CREATED": {
        "title": "New Booking Request 🙏",
        "body": "You have a new booking request for {scheduled_date}. Please accept or decline within 2 hours.",
        "sms": "PanditBooking: New booking request for {scheduled_date}. Open app to accept.",
    },
    "BOOKING_COMPLETED": {
        "title": "Pooja Completed ✨",
        "body": "The pooja for booking #{booking_number} is marked as completed. Please share your experience.",
        "sms": None,
    },
    "PAYMENT_SUCCESS": {
        "title": "Payment Successful ✅",
        "body": "Payment of ₹{amount} received for booking #{booking_number}.",
        "sms": "PanditBooking: Payment of Rs.{amount} received. Booking #{booking_number}",
    },
    "ACCOUNT_VERIFIED": {
        "title": "Account Verified! 🎉",
        "body": "Congratulations! Your pandit profile has been verified. You can now accept bookings.",
        "sms": "PanditBooking: Your profile has been verified. You can now accept bookings!",
    },
}


async def dispatch_notification(
    db: AsyncSession,
    user: User,
    notification_type: str,
    template_vars: dict = None,
    booking_id: str = None,
    send_push: bool = True,
    send_sms_: bool = True,
    send_email_: bool = True,
):
    """
    Central notification dispatcher.
    1. Save to DB (in-app)
    2. Send push via FCM
    3. Send SMS via Twilio
    4. Send email via Resend
    """
    from shared.models.models import NotificationType

    template = TEMPLATES.get(notification_type, {})
    vars_ = template_vars or {}

    title = template.get("title", "Notification").format(**vars_)
    body = template.get("body", "").format(**vars_)

    # 1. Save in-app notification
    try:
        notif_type = NotificationType[notification_type]
    except KeyError:
        notif_type = NotificationType.BOOKING_CREATED

    notif = Notification(
        user_id=user.id,
        booking_id=booking_id,
        type=notif_type,
        title=title,
        body=body,
    )
    db.add(notif)

    # 2. FCM Push
    if send_push and user.fcm_token:
        success = await send_fcm_push(user.fcm_token, title, body, {"booking_id": booking_id or ""})
        notif.sent_push = success

    # 3. SMS
    sms_template = template.get("sms")
    if send_sms_ and sms_template and user.phone:
        sms_body = sms_template.format(**vars_)
        success = await send_sms(user.phone, sms_body)
        notif.sent_sms = success

    # 4. Email
    if send_email_:
        subject = title
        html_body = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: #FF6B00; padding: 20px; border-radius: 8px 8px 0 0; text-align: center;">
                <h1 style="color: white; margin: 0;">🕉️ Pandit Booking</h1>
            </div>
            <div style="background: white; padding: 24px; border: 1px solid #eee; border-radius: 0 0 8px 8px;">
                <h2 style="color: #333;">{title}</h2>
                <p style="color: #666; line-height: 1.6;">{body}</p>
                <p style="color: #999; font-size: 12px; margin-top: 24px;">
                    You received this email because you have an account on Pandit Booking.
                </p>
            </div>
        </div>
        """
        success = await send_email(user.email, user.name, subject, html_body)
        notif.sent_email = success

    await db.flush()


# ── REST Endpoints ────────────────────────────────────────────

@router.get("", response_model=list[NotificationResponse])
async def get_my_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get authenticated user's in-app notifications."""
    query = (
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
    )

    if unread_only:
        query = query.where(Notification.is_read == False)

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return [NotificationResponse.model_validate(n) for n in result.scalars()]


@router.post("/{notification_id}/read", response_model=MessageResponse)
async def mark_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == current_user.id)
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return MessageResponse(message="Marked as read")


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return MessageResponse(message="All notifications marked as read")


@router.get("/unread-count")
async def unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import func
    count = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
    )
    return {"unread_count": count or 0}
