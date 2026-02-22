"""
tests/test_notifications.py
Tests for in-app notification management: listing, marking as read, unread count.
"""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import Notification, NotificationType, User
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_get_notifications_empty(client: AsyncClient, user: User):
    """User with no notifications gets empty list."""
    response = await client.get("/notifications", headers=auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_notifications_returns_own(
    client: AsyncClient, user: User, db: AsyncSession
):
    """User sees their own notifications only."""
    notif = Notification(
        id=uuid.uuid4(),
        user_id=user.id,
        type=NotificationType.BOOKING_CONFIRMED,
        title="Booking Confirmed",
        body="Your booking PB-TEST-001 is confirmed.",
        is_read=False,
    )
    db.add(notif)
    await db.commit()

    response = await client.get("/notifications", headers=auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    ids = [n["id"] for n in data["items"]]
    assert str(notif.id) in ids


@pytest.mark.asyncio
async def test_unread_count(client: AsyncClient, user: User, db: AsyncSession):
    """Unread count returns correct number of unread notifications."""
    for i in range(3):
        db.add(Notification(
            id=uuid.uuid4(),
            user_id=user.id,
            type=NotificationType.BOOKING_CONFIRMED,
            title=f"Notification {i}",
            body=f"Body {i}",
            is_read=False,
        ))
    await db.commit()

    response = await client.get("/notifications/unread-count", headers=auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["unread_count"] == 3


@pytest.mark.asyncio
async def test_mark_single_notification_read(
    client: AsyncClient, user: User, db: AsyncSession
):
    """Marking a notification as read updates is_read to True."""
    notif = Notification(
        id=uuid.uuid4(),
        user_id=user.id,
        type=NotificationType.PAYMENT_SUCCESS,
        title="Payment received",
        body="₹2200 received",
        is_read=False,
    )
    db.add(notif)
    await db.commit()

    response = await client.post(
        f"/notifications/{notif.id}/read",
        headers=auth_headers(user),
    )
    assert response.status_code == 200

    # Verify unread count decreased
    count_response = await client.get("/notifications/unread-count", headers=auth_headers(user))
    assert count_response.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_mark_all_notifications_read(
    client: AsyncClient, user: User, db: AsyncSession
):
    """Bulk mark-all-as-read updates all unread notifications."""
    for i in range(5):
        db.add(Notification(
            id=uuid.uuid4(),
            user_id=user.id,
            type=NotificationType.BOOKING_REMINDER,
            title=f"Reminder {i}",
            body="Reminder body",
            is_read=False,
        ))
    await db.commit()

    response = await client.post("/notifications/read-all", headers=auth_headers(user))
    assert response.status_code == 200

    count_response = await client.get("/notifications/unread-count", headers=auth_headers(user))
    assert count_response.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_filter_unread_only(client: AsyncClient, user: User, db: AsyncSession):
    """unread_only=true filter returns only unread notifications."""
    # 2 read, 3 unread
    for i in range(2):
        db.add(Notification(
            id=uuid.uuid4(), user_id=user.id, type=NotificationType.BOOKING_CONFIRMED,
            title=f"Read {i}", body="body", is_read=True,
            read_at=datetime.now(timezone.utc),
        ))
    for i in range(3):
        db.add(Notification(
            id=uuid.uuid4(), user_id=user.id, type=NotificationType.BOOKING_CONFIRMED,
            title=f"Unread {i}", body="body", is_read=False,
        ))
    await db.commit()

    response = await client.get(
        "/notifications",
        headers=auth_headers(user),
        params={"unread_only": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(not n["is_read"] for n in data["items"])
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_cannot_read_other_users_notification(
    client: AsyncClient, user: User, pandit_user: User, db: AsyncSession
):
    """User cannot mark another user's notification as read."""
    other_notif = Notification(
        id=uuid.uuid4(),
        user_id=pandit_user.id,  # belongs to pandit_user
        type=NotificationType.BOOKING_CONFIRMED,
        title="Not yours",
        body="body",
        is_read=False,
    )
    db.add(other_notif)
    await db.commit()

    response = await client.post(
        f"/notifications/{other_notif.id}/read",
        headers=auth_headers(user),  # user, not pandit_user
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_notifications_requires_auth(client: AsyncClient):
    response = await client.get("/notifications")
    assert response.status_code == 401
