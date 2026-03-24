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


class TokenPrincipal:
    """Lightweight authenticated principal from JWT claims only."""

    def __init__(self, token_data: TokenData):
        self.id: str = token_data.user_id
        self.role: UserRole = token_data.role
        self.email: str = token_data.email
        self.is_active: bool = True


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


async def get_current_user_record(
    token_data: TokenData = Depends(get_token_data),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Auth-service/local dependency that loads the persisted user record."""
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


async def get_current_principal(
    token_data: TokenData = Depends(get_token_data),
) -> TokenPrincipal:
    """JWT-only principal for services that don't need direct user-table lookup."""
    return TokenPrincipal(token_data)


async def get_current_user(
    principal: TokenPrincipal = Depends(get_current_principal),
) -> TokenPrincipal:
    """Default authenticated identity for microservices: JWT claims only."""
    return principal


async def get_current_active_user(
    current_user: TokenPrincipal = Depends(get_current_user),
) -> TokenPrincipal:
    return current_user


class RoleRequired:
    """Dependency factory for role-based access control."""

    def __init__(self, *roles: UserRole):
        self.roles = roles

    async def __call__(
        self,
        current_user: TokenPrincipal = Depends(get_current_user),
    ) -> TokenPrincipal:
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


class RolePrincipalRequired:
    """Role guard that works on JWT principal without user-table lookup."""

    def __init__(self, *roles: UserRole):
        self.roles = roles

    async def __call__(
        self,
        principal: TokenPrincipal = Depends(get_current_principal),
    ) -> TokenPrincipal:
        if principal.role not in self.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {[r.value for r in self.roles]}",
            )
        return principal


require_user_principal = RolePrincipalRequired(UserRole.USER, UserRole.PANDIT, UserRole.ADMIN)
require_pandit_principal = RolePrincipalRequired(UserRole.PANDIT, UserRole.ADMIN)
require_admin_principal = RolePrincipalRequired(UserRole.ADMIN)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    redis=Depends(get_redis),
) -> Optional[TokenPrincipal]:
    """Returns JWT principal if authenticated, None otherwise."""
    if not credentials:
        return None
    try:
        payload = verify_access_token(credentials.credentials)
        jti = payload.get("jti")
        if jti and await redis.exists(f"jwt_revoked:{jti}"):
            return None
        return TokenPrincipal(TokenData(payload))
    except JWTError:
        return None
