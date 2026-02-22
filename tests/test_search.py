"""
tests/test_search.py
Tests for pandit search (geo + filters) and pooja search.
"""

import pytest
from httpx import AsyncClient

from shared.models.models import Pooja, User
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_search_pandits_missing_coords_returns_422(client: AsyncClient):
    """Geo search without lat/lng should return 422 validation error."""
    response = await client.get("/search/pandits")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_pandits_with_coords(client: AsyncClient):
    """Geo search with valid coordinates returns 200 (may return 0 results in test)."""
    response = await client.get(
        "/search/pandits",
        params={"lat": 25.3176, "lng": 82.9739, "radius_km": 30},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_search_pandits_with_filters(client: AsyncClient):
    """Search with language and experience filters is accepted."""
    response = await client.get(
        "/search/pandits",
        params={
            "lat": 25.3176,
            "lng": 82.9739,
            "radius_km": 50,
            "languages": "Hindi",
            "experience_min": 5,
            "price_max": 5000,
            "sort_by": "rating",
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_pandits_invalid_sort_returns_422(client: AsyncClient):
    """Invalid sort_by value should be rejected."""
    response = await client.get(
        "/search/pandits",
        params={"lat": 25.3176, "lng": 82.9739, "sort_by": "invalid_sort_field"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_poojas_returns_list(client: AsyncClient, pooja: Pooja):
    """Pooja search returns the seeded pooja."""
    response = await client.get("/search/poojas")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    names = [p["name_en"] for p in data]
    assert "Ganesh Puja" in names


@pytest.mark.asyncio
async def test_search_poojas_by_query(client: AsyncClient, pooja: Pooja):
    """Text search on poojas works with partial name match."""
    response = await client.get("/search/poojas", params={"q": "Ganesh"})
    assert response.status_code == 200
    data = response.json()
    assert any("Ganesh" in p["name_en"] for p in data)


@pytest.mark.asyncio
async def test_search_poojas_by_category(client: AsyncClient, pooja: Pooja):
    """Filtering poojas by category works."""
    response = await client.get("/search/poojas", params={"category": "GRIHA"})
    assert response.status_code == 200
    data = response.json()
    # The fixture pooja has category GRIHA
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_pandit_suggestions_autocomplete(client: AsyncClient):
    """Suggestions endpoint returns a list (may be empty in test env)."""
    response = await client.get("/search/pandits/suggestions", params={"q": "Ram"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) <= 5  # Max 5 suggestions
