"""
tests/test_pandits.py
Tests for pandit profile management, availability, and public profile visibility.
"""

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from shared.models.models import PanditProfile, User, VerificationStatus
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_public_profile_visible_when_verified(
    client: AsyncClient, pandit_profile: PanditProfile, pandit_user: User
):
    """Verified pandit profile is publicly accessible."""
    response = await client.get(f"/pandits/{pandit_profile.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["city"] == pandit_profile.city
    assert data["experience_years"] == pandit_profile.experience_years


@pytest.mark.asyncio
async def test_unverified_pandit_returns_404(
    client: AsyncClient, db, pandit_user: User
):
    """Unverified/pending pandits are not visible to the public."""
    pending_profile = PanditProfile(
        id=uuid.uuid4(),
        user_id=pandit_user.id,
        city="Mumbai",
        verification_status=VerificationStatus.PENDING,
        is_available=True,
        base_fee=1000,
    )
    db.add(pending_profile)
    await db.commit()

    response = await client.get(f"/pandits/{pending_profile.id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_nonexistent_pandit_returns_404(client: AsyncClient):
    response = await client.get(f"/pandits/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pandit_can_update_own_profile(
    client: AsyncClient, pandit_user: User, pandit_profile: PanditProfile
):
    response = await client.put(
        "/pandits/me/profile",
        headers=auth_headers(pandit_user),
        json={
            "bio": "Updated bio with more details about my practice.",
            "experience_years": 12,
            "city": "Prayagraj",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["bio"] == "Updated bio with more details about my practice."
    assert data["experience_years"] == 12


@pytest.mark.asyncio
async def test_regular_user_cannot_update_pandit_profile(
    client: AsyncClient, user: User
):
    response = await client.put(
        "/pandits/me/profile",
        headers=auth_headers(user),
        json={"bio": "Trying to be a pandit"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_set_availability_slots(
    client: AsyncClient, pandit_user: User, pandit_profile: PanditProfile
):
    """Pandit can set availability slots for future dates."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = {
        "slots": [
            {"date": tomorrow, "start_time": "09:00", "end_time": "11:00"},
            {"date": tomorrow, "start_time": "14:00", "end_time": "17:00"},
        ]
    }
    response = await client.put(
        "/pandits/me/availability",
        headers=auth_headers(pandit_user),
        json=payload,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_pandit_calendar(
    client: AsyncClient, pandit_user: User, pandit_profile: PanditProfile
):
    response = await client.get(
        "/pandits/me/calendar",
        headers=auth_headers(pandit_user),
        params={"month": date.today().month, "year": date.today().year},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_pandit_earnings(
    client: AsyncClient, pandit_user: User, pandit_profile: PanditProfile
):
    response = await client.get("/pandits/me/earnings", headers=auth_headers(pandit_user))
    assert response.status_code == 200
    data = response.json()
    assert "total_earned" in data
    assert "pending_payout" in data


@pytest.mark.asyncio
async def test_user_cannot_access_pandit_earnings(client: AsyncClient, user: User):
    response = await client.get("/pandits/me/earnings", headers=auth_headers(user))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_pandit_location(
    client: AsyncClient, pandit_user: User, pandit_profile: PanditProfile
):
    """Pandit can update their real-time GPS location."""
    response = await client.put(
        "/pandits/me/location",
        headers=auth_headers(pandit_user),
        json={"latitude": 25.3176, "longitude": 82.9739},
    )
    assert response.status_code == 200
