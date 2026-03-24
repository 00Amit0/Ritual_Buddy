"""
shared/utils/third_party.py
Centralized third-party integrations and configuration checks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)


def is_razorpay_configured() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def is_resend_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.EMAIL_FROM)


def is_twilio_configured() -> bool:
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_FROM_NUMBER
    )


def is_firebase_configured() -> bool:
    if not settings.FIREBASE_CREDENTIALS_PATH:
        return False
    return Path(settings.FIREBASE_CREDENTIALS_PATH).exists()


def integration_status() -> dict[str, str]:
    return {
        "razorpay": "ok" if is_razorpay_configured() else "missing_config",
        "firebase": "ok" if is_firebase_configured() else "missing_config",
        "twilio": "ok" if is_twilio_configured() else "missing_config",
        "resend": "ok" if is_resend_configured() else "missing_config",
    }


def validate_integrations_for_env() -> None:
    """
    Enforce required integrations in strict production mode.
    By default only payment (Razorpay) is required for API startup.
    """
    if not settings.is_production:
        return
    if not settings.THIRD_PARTY_STRICT_STARTUP:
        return

    if not is_razorpay_configured():
        raise RuntimeError(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )


def get_razorpay_client():
    if not is_razorpay_configured():
        raise RuntimeError("Razorpay credentials are not configured")
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def send_fcm_push(fcm_token: str, title: str, body: str, data: Optional[dict] = None) -> bool:
    if not is_firebase_configured():
        return False
    try:
        import firebase_admin
        import firebase_admin.credentials as credentials
        from firebase_admin import messaging

        if not firebase_admin._apps:
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1)
                )
            ),
        )
        messaging.send(message)
        return True
    except Exception as exc:
        logger.warning("FCM send failed: %s", exc)
        return False


def send_sms(phone_number: str, body: str) -> bool:
    if not is_twilio_configured():
        return False
    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=body,
            from_=settings.TWILIO_FROM_NUMBER,
            to=phone_number if phone_number.startswith("+") else f"+91{phone_number}",
        )
        return True
    except Exception as exc:
        logger.warning("Twilio SMS failed: %s", exc)
        return False


def send_email(to_email: str, to_name: str, subject: str, html_body: str) -> bool:
    if not is_resend_configured():
        return False
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send(
            {
                "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
                "to": [f"{to_name} <{to_email}>"],
                "subject": subject,
                "html": html_body,
            }
        )
        return True
    except Exception as exc:
        logger.warning("Resend email failed: %s", exc)
        return False
