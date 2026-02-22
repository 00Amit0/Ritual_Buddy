"""
tests/test_users.py
Tests for user profile management, address book, and saved pandits.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.models import PanditProfile, User
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_get_user_profile(client: AsyncClient, user: User):
    response = await client.get("/users/me", headers=auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email
    assert data["name"] == user.name


@pytest.mark.asyncio
async def test_update_user_name(client: AsyncClient, user: User):
    response = await client.put(
        "/users/me",
        headers=auth_headers(user),
        json={"name": "Updated Name"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_user_phone(client: AsyncClient, user: User):
    response = await client.put(
        "/users/me",
        headers=auth_headers(user),
        json={"phone": "+919999988888"},
    )
    assert response.status_code == 200
    assert response.json()["phone"] == "+919999988888"


@pytest.mark.asyncio
async def test_update_empty_body_is_noop(client: AsyncClient, user: User):
    """Sending an empty dict should not error — it's a valid no-op."""
    response = await client.put("/users/me", headers=auth_headers(user), json={})
    assert response.status_code == 200
    assert response.json()["email"] == user.email


@pytest.mark.asyncio
async def test_get_user_requires_auth(client: AsyncClient):
    response = await client.get("/users/me")
    assert response.status_code == 401


# ── Address Book ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_address(client: AsyncClient, user: User):
    payload = {
        "label": "Home",
        "address_line1": "123 MG Road",
        "city": "Varanasi",
        "state": "Uttar Pradesh",
        "pincode": "221001",
        "is_default": True,
    }
    response = await client.post("/users/me/addresses", headers=auth_headers(user), json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["label"] == "Home"
    assert data["city"] == "Varanasi"
    assert data["is_default"] is True


@pytest.mark.asyncio
async def test_list_addresses(client: AsyncClient, user: User):
    # Add two addresses
    for i in range(2):
        await client.post(
            "/users/me/addresses",
            headers=auth_headers(user),
            json={"label": f"Address {i}", "address_line1": f"{i} Road", "city": "Delhi",
                  "state": "Delhi", "pincode": "110001", "is_default": False},
        )

    response = await client.get("/users/me/addresses", headers=auth_headers(user))
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_only_one_default_address(client: AsyncClient, user: User):
    """Setting a new address as default should clear all other defaults."""
    base = {"address_line1": "1 Road", "city": "Mumbai", "state": "Maharashtra",
            "pincode": "400001"}

    await client.post("/users/me/addresses", headers=auth_headers(user),
                      json={**base, "label": "Home", "is_default": True})
    await client.post("/users/me/addresses", headers=auth_headers(user),
                      json={**base, "label": "Office", "is_default": True})

    response = await client.get("/users/me/addresses", headers=auth_headers(user))
    addresses = response.json()
    defaults = [a for a in addresses if a["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["label"] == "Office"


# ── Saved Pandits ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_unsave_pandit(client: AsyncClient, user: User, pandit_profile: PanditProfile):
    pandit_id = str(pandit_profile.id)

    # Save
    save_response = await client.post(
        f"/users/me/saved-pandits/{pandit_id}",
        headers=auth_headers(user),
    )
    assert save_response.status_code == 200

    # Verify in list
    list_response = await client.get("/users/me/saved-pandits", headers=auth_headers(user))
    assert list_response.status_code == 200
    saved_ids = [p["pandit_id"] for p in list_response.json()]
    assert pandit_id in saved_ids

    # Unsave
    unsave_response = await client.delete(
        f"/users/me/saved-pandits/{pandit_id}",
        headers=auth_headers(user),
    )
    assert unsave_response.status_code == 200

    # Verify removed
    list_response2 = await client.get("/users/me/saved-pandits", headers=auth_headers(user))
    saved_ids2 = [p["pandit_id"] for p in list_response2.json()]
    assert pandit_id not in saved_ids2


@pytest.mark.asyncio
async def test_save_same_pandit_twice_is_idempotent(client: AsyncClient, user: User, pandit_profile: PanditProfile):
    pandit_id = str(pandit_profile.id)
    r1 = await client.post(f"/users/me/saved-pandits/{pandit_id}", headers=auth_headers(user))
    r2 = await client.post(f"/users/me/saved-pandits/{pandit_id}", headers=auth_headers(user))
    assert r1.status_code == 200
    assert r2.status_code == 200  # idempotent, not 409

    # Should only appear once in list
    list_response = await client.get("/users/me/saved-pandits", headers=auth_headers(user))
    saved_ids = [p["pandit_id"] for p in list_response.json()]
    assert saved_ids.count(pandit_id) == 1


@pytest.mark.asyncio
async def test_save_nonexistent_pandit_returns_404(client: AsyncClient, user: User):
    fake_id = str(uuid.uuid4())
    response = await client.post(f"/users/me/saved-pandits/{fake_id}", headers=auth_headers(user))
    assert response.status_code == 404
