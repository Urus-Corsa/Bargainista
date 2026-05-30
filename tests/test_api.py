"""API integration tests for app/api/routes.py.

No real database, Celery broker, or MCP server. All external dependencies
are mocked via dependency overrides and unittest.mock.patch.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_db_session() -> AsyncSession:
    """Return an AsyncSession mock that handles the analyze route's DB calls.

    refresh() must set run.id to a UUID so the route can return it.
    """
    session = AsyncMock(spec=AsyncSession)

    async def fake_refresh(obj):
        if not hasattr(obj, "id") or obj.id is None:
            obj.id = uuid.uuid4()

    session.refresh.side_effect = fake_refresh
    return session


@pytest.fixture
def mock_db():
    """Override the get_db FastAPI dependency with a mock session."""
    mock_session = _mock_db_session()

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    yield mock_session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/analyze — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_valid_vin_returns_202_with_run_id(mock_db, client):
    payload = {
        "input_method": "vin",
        "vin": "1HGBH41JXMN109186",
        "mileage": 50000,
        "asking_price": 18000,
        "location": "Austin, TX",
        "user_damage_notes": "minor scratch",
    }

    with patch("app.api.routes.run_analysis_task") as mock_task:
        mock_task.delay = MagicMock()
        response = await client.post("/api/analyze", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert "run_id" in body
    # Verify it's a valid UUID
    uuid.UUID(body["run_id"])
    mock_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_analyze_manual_tab_valid(mock_db, client):
    payload = {
        "input_method": "manual",
        "year": 2019,
        "make": "Toyota",
        "model": "Camry",
        "mileage": 60000,
        "asking_price": 17000,
        "location": "Dallas, TX",
        "user_damage_notes": "dent on rear bumper",
    }

    with patch("app.api.routes.run_analysis_task") as mock_task:
        mock_task.delay = MagicMock()
        response = await client.post("/api/analyze", json=payload)

    assert response.status_code == 202


# ---------------------------------------------------------------------------
# POST /api/analyze — 422 validation failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_missing_vehicle_identity_returns_422(mock_db, client):
    # No VIN and no year/make/model — fails ListingInput vehicle_identity_present validator
    payload = {
        "input_method": "manual",
        "mileage": 50000,
        "asking_price": 18000,
        "location": "Austin, TX",
        "user_damage_notes": "scratch",
    }
    response = await client.post("/api/analyze", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_missing_mileage_returns_422(mock_db, client):
    payload = {
        "input_method": "manual",
        "year": 2020,
        "make": "Honda",
        "model": "Civic",
        "asking_price": 15000,
        "location": "Austin, TX",
        "user_damage_notes": "scratch",
    }
    response = await client.post("/api/analyze", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_missing_asking_price_returns_422(mock_db, client):
    payload = {
        "input_method": "manual",
        "year": 2020,
        "make": "Honda",
        "model": "Civic",
        "mileage": 30000,
        "location": "Austin, TX",
        "user_damage_notes": "scratch",
    }
    response = await client.post("/api/analyze", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_no_images_no_damage_notes_returns_422(mock_db, client):
    # ListingInput requires at least one image or user_damage_notes
    payload = {
        "input_method": "manual",
        "year": 2020,
        "make": "Honda",
        "model": "Civic",
        "mileage": 30000,
        "asking_price": 15000,
        "location": "Austin, TX",
    }
    response = await client.post("/api/analyze", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/vin/{vin}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decode_vin_found(client):
    mock_specs = {"year": "2022", "make": "Honda", "model": "Civic", "trim": "EX"}

    with patch("app.api.routes.call_tool", new=AsyncMock(return_value=mock_specs)):
        response = await client.get("/api/vin/1HGBH41JXMN109186")

    assert response.status_code == 200
    body = response.json()
    assert body["make"] == "Honda"
    assert body["model"] == "Civic"
    assert body["year"] == "2022"


@pytest.mark.asyncio
async def test_decode_vin_not_found_returns_404(client):
    with patch("app.api.routes.call_tool", new=AsyncMock(return_value=None)):
        response = await client.get("/api/vin/BADVIN00000000000")

    assert response.status_code == 404
