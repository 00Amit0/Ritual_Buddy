"""
shared/utils/security.py
JWT creation/verification, password hashing, and security helpers.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from config.settings import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── JWT ───────────────────────────────────────────────────────

def create_access_token(
    user_id: str,
    role: str,
    email: str,
    extra: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Create a signed JWT access token.
    Returns (token, jti) — jti is used for deny-listing on logout.
    """
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": str(user_id),
        "role": role,
        "email": email,
        "jti": jti,
        "iat": now,
        "exp": expire,
        "type": "access",
        **(extra or {}),
    }

    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def create_refresh_token() -> tuple[str, str]:
    """
    Create a cryptographically random refresh token.
    Returns (raw_token, hashed_token) — store only the hash in DB.
    """
    raw_token = secrets.token_urlsafe(64)
    hashed = hash_token(raw_token)
    return raw_token, hashed


def hash_token(token: str) -> str:
    """SHA-256 hash for securely storing refresh tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


def verify_access_token(token: str) -> dict:
    """
    Decode and verify a JWT access token.
    Raises JWTError on invalid/expired token.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("type") != "access":
            raise JWTError("Invalid token type")
        return payload
    except JWTError:
        raise


def get_token_remaining_ttl(payload: dict) -> int:
    """Returns seconds until token expiry. Used for JWT deny-list TTL."""
    exp = payload.get("exp", 0)
    remaining = exp - datetime.now(timezone.utc).timestamp()
    return max(0, int(remaining))


# ── Password (for admin local auth fallback) ──────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ── Razorpay Webhook Signature ────────────────────────────────

def verify_razorpay_signature(
    order_id: str,
    payment_id: str,
    signature: str,
) -> bool:
    """Verify Razorpay payment signature using HMAC-SHA256."""
    import hmac
    body = f"{order_id}|{payment_id}"
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_razorpay_webhook_signature(payload_body: bytes, signature: str) -> bool:
    """Verify Razorpay webhook body signature."""
    import hmac
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
