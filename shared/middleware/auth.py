"""
shared/middleware/auth.py
FastAPI dependency functions for authentication and authorization.
JWT is validated here. Downstream services trust X-User-* headers.
"""

from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config.database import get_db
from config.redis_client import get_redis
from shared.models.models import User, UserRole
from shared.utils.security import verify_access_token

security = HTTPBearer(auto_error=False)


class TokenData:
    def __init__(self, payload: dict):
        self.user_id: str = payload["sub"]
        self.role: UserRole = UserRole(payload["role"])
        self.email: str = payload["email"]
        self.jti: str = payload["jti"]


async def get_token_data(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    redis=Depends(get_redis),
) -> TokenData:
    """
    Extract and validate JWT from Authorization header.
    Checks deny-list in Redis to handle revoked tokens (logout).
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = verify_access_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if token has been revoked (logged out)
    jti = payload.get("jti")
    if jti and await redis.exists(f"jwt_revoked:{jti}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    return TokenData(payload)


async def get_current_user(
    token_data: TokenData = Depends(get_token_data),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Load full User object from database using JWT sub claim."""
    result = await db.execute(select(User).where(User.id == token_data.user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user


class RoleRequired:
    """Dependency factory for role-based access control."""

    def __init__(self, *roles: UserRole):
        self.roles = roles

    async def __call__(
        self,
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role not in self.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {[r.value for r in self.roles]}",
            )
        return current_user


# Convenience role dependencies
require_user = RoleRequired(UserRole.USER, UserRole.PANDIT, UserRole.ADMIN)
require_pandit = RoleRequired(UserRole.PANDIT, UserRole.ADMIN)
require_admin = RoleRequired(UserRole.ADMIN)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> Optional[User]:
    """Returns current user if authenticated, None otherwise. For public endpoints."""
    if not credentials:
        return None
    try:
        payload = verify_access_token(credentials.credentials)
        jti = payload.get("jti")
        if jti and await redis.exists(f"jwt_revoked:{jti}"):
            return None
        result = await db.execute(select(User).where(User.id == payload["sub"]))
        return result.scalar_one_or_none()
    except JWTError:
        return None
