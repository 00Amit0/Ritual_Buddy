"""
services/auth/router.py
OAuth2 (Google) authentication endpoints.
Implements: Login → Callback → JWT issue → Refresh → Logout
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.redis_client import get_redis
from config.settings import settings
from shared.middleware.auth import get_current_user
from shared.models.models import OAuthProvider, RefreshToken, User, UserRole
from shared.schemas.schemas import AuthCallbackResponse, MessageResponse, TokenResponse, UserResponse
from shared.utils.security import (
    create_access_token,
    create_refresh_token,
    get_token_remaining_ttl,
    hash_token,
    verify_access_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── OAuth Setup ───────────────────────────────────────────────
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
    redirect_uri=settings.GOOGLE_REDIRECT_URI,
)


# ── Helper ────────────────────────────────────────────────────

async def _get_or_create_user(
    db: AsyncSession,
    oauth_provider: OAuthProvider,
    oauth_id: str,
    email: str,
    name: str,
    avatar_url: Optional[str],
) -> User:
    """Get existing user by OAuth ID or create a new one."""
    # Try find by oauth provider + id
    result = await db.execute(
        select(User).where(
            User.oauth_provider == oauth_provider,
            User.oauth_id == oauth_id,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        # Try find by email (different provider, same email)
        result = await db.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            # Link this OAuth provider to existing account
            existing.oauth_provider = oauth_provider
            existing.oauth_id = oauth_id
            existing.avatar_url = avatar_url or existing.avatar_url
            return existing

        # Brand new user
        user = User(
            oauth_provider=oauth_provider,
            oauth_id=oauth_id,
            email=email,
            name=name,
            avatar_url=avatar_url,
            role=UserRole.USER,
        )
        db.add(user)
        await db.flush()

    return user


async def _issue_tokens(
    user: User,
    db: AsyncSession,
    response: Response,
    request: Request,
) -> tuple[str, str]:
    """Issue access + refresh tokens. Store refresh token in DB and set cookie."""
    # Access token
    access_token, jti = create_access_token(
        user_id=str(user.id),
        role=user.role.value,
        email=user.email,
    )

    # Refresh token
    raw_refresh, hashed_refresh = create_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )

    db_token = RefreshToken(
        user_id=user.id,
        token_hash=hashed_refresh,
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    db.add(db_token)

    # Set httpOnly cookie for refresh token (web clients)
    response.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth/refresh",
    )

    return access_token, raw_refresh


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/google", summary="Initiate Google OAuth2 login")
async def google_login(request: Request):
    """
    Redirects the user to Google's OAuth2 consent page.
    The client should open this URL in a browser/webview.
    """
    redirect_uri = settings.GOOGLE_REDIRECT_URI
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get(
    "/google/callback",
    response_model=AuthCallbackResponse,
    summary="Google OAuth2 callback",
)
async def google_callback(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Handles Google OAuth2 callback. Issues JWT access token + refresh token.
    On success, redirects to frontend with tokens (or returns JSON for mobile).
    """
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth error: {e.error}",
        )
    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not fetch user info from Google",
        )

    user = await _get_or_create_user(
        db=db,
        oauth_provider=OAuthProvider.GOOGLE,
        oauth_id=userinfo["sub"],
        email=userinfo["email"],
        name=userinfo.get("name", ""),
        avatar_url=userinfo.get("picture"),
    )

    access_token, raw_refresh = await _issue_tokens(user, db, response, request)
    await db.commit()

    return AuthCallbackResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(
    request: Request,
    response: Response,
    # Accept from cookie (web) or request body (mobile)
    refresh_token_cookie: Optional[str] = Cookie(None, alias="refresh_token"),
    db: AsyncSession = Depends(get_db),
):
    """
    Issue a new access token using a valid refresh token.
    Implements refresh token rotation — old token is revoked.
    """
    # Get token from cookie or Authorization header
    raw_token = refresh_token_cookie
    if not raw_token:
        body = await request.json()
        raw_token = body.get("refresh_token")

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )

    # Find token in DB
    token_hash = hash_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked == False,
        )
    )
    db_token = result.scalar_one_or_none()

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked refresh token",
        )

    if db_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    # Load user
    result = await db.execute(select(User).where(User.id == db_token.user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Rotate: revoke old token, issue new ones
    db_token.is_revoked = True

    access_token, raw_refresh = await _issue_tokens(user, db, response, request)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=MessageResponse, summary="Logout user")
async def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
    refresh_token_cookie: Optional[str] = Cookie(None, alias="refresh_token"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Revoke refresh token + add JWT to deny-list in Redis.
    Clears httpOnly cookie.
    """
    # Add current access token JTI to Redis deny-list
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = verify_access_token(auth_header[7:])
            jti = payload.get("jti")
            if jti:
                ttl = get_token_remaining_ttl(payload)
                if ttl > 0:
                    await redis.setex(f"jwt_revoked:{jti}", ttl, "1")
        except Exception:
            pass

    # Revoke refresh token
    if refresh_token_cookie:
        token_hash = hash_token(refresh_token_cookie)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        db_token = result.scalar_one_or_none()
        if db_token:
            db_token.is_revoked = True

    # Clear cookie
    response.delete_cookie(key="refresh_token", path="/auth/refresh")
    await db.commit()

    return MessageResponse(message="Logged out successfully")


@router.get("/me", response_model=UserResponse, summary="Get current user")
async def get_me(current_user: User = Depends(get_current_user)):
    """Returns the authenticated user's profile."""
    return UserResponse.model_validate(current_user)
