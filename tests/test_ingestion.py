"""Unit tests for app/utils/ingestion.py.

Covers pure Python logic (_combine_history_text, _resize_if_needed) and
async logic (prepare_listing) with all external I/O mocked.
"""

from __future__ import annotations

import base64
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.models.schemas import ListingInput
from app.utils.ingestion import _combine_history_text, _resize_if_needed, prepare_listing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_listing(**kwargs) -> ListingInput:
    defaults = {
        "input_method": "manual",
        "year": 2020,
        "make": "Toyota",
        "model": "Camry",
        "mileage": 50_000,
        "asking_price": 20_000,
        "location": "Austin, TX",
        "user_damage_notes": "minor scratch on door",
    }
    defaults.update(kwargs)
    return ListingInput(**defaults)


def _make_b64_image(width: int, height: int) -> str:
    """Create a small solid-color JPEG and return it as a base64 string."""
    img = Image.new("RGB", (width, height), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ---------------------------------------------------------------------------
# _combine_history_text
# ---------------------------------------------------------------------------


def test_combine_no_docs_returns_report_text():
    listing = _make_listing(history_report_text="Some Carfax notes")
    result = _combine_history_text(listing)
    assert result == "Some Carfax notes"


def test_combine_no_docs_no_report_returns_none():
    listing = _make_listing()
    result = _combine_history_text(listing)
    assert result is None


def test_combine_docs_only_no_report():
    listing = _make_listing(
        history_document_texts=["[Document: carfax.pdf]\nAccident in 2021"]
    )
    result = _combine_history_text(listing)
    assert result == "[Document: carfax.pdf]\nAccident in 2021"
    assert "[User notes]" not in result


def test_combine_report_and_docs():
    listing = _make_listing(
        history_report_text="My notes",
        history_document_texts=[
            "[Document: carfax.pdf]\nAccident in 2021",
            "[Document: service.pdf]\nOil change 2023",
        ],
    )
    result = _combine_history_text(listing)
    assert result is not None
    assert "[User notes]\nMy notes" in result
    assert "[Document: carfax.pdf]" in result
    assert "[Document: service.pdf]" in result


def test_combine_multiple_docs_separator():
    listing = _make_listing(
        history_document_texts=["[Document: a.pdf]\nfoo", "[Document: b.pdf]\nbar"]
    )
    result = _combine_history_text(listing)
    assert result is not None
    parts = result.split("\n\n")
    assert len(parts) == 2


# ---------------------------------------------------------------------------
# _resize_if_needed
# ---------------------------------------------------------------------------


def test_resize_small_image_unchanged():
    b64 = _make_b64_image(100, 100)
    result = _resize_if_needed(b64)
    assert result == b64


def test_resize_large_image_shrinks():
    b64 = _make_b64_image(3000, 2000)
    result = _resize_if_needed(b64)
    raw = base64.b64decode(result)
    img = Image.open(io.BytesIO(raw))
    w, h = img.size
    assert w <= 1568
    assert h <= 1568


def test_resize_exactly_at_limit_unchanged():
    b64 = _make_b64_image(1568, 1000)
    result = _resize_if_needed(b64)
    assert result == b64


# ---------------------------------------------------------------------------
# prepare_listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_listing_vin_enrichment():
    listing = _make_listing(vin="1HGBH41JXMN109186", year=None, make=None, model=None)
    mock_specs = {"year": "2022", "make": "Honda", "model": "Civic", "trim": "EX"}
    mock_images = ["fakeb64"]

    with (
        patch("app.utils.ingestion.resolve_vin", new=AsyncMock(return_value=mock_specs)),
        patch("app.utils.ingestion.normalise_images", new=AsyncMock(return_value=mock_images)),
    ):
        enriched, images = await prepare_listing(listing)

    assert enriched.year == 2022
    assert enriched.make == "Honda"
    assert enriched.model == "Civic"
    assert enriched.trim == "EX"
    assert images == mock_images


@pytest.mark.asyncio
async def test_prepare_listing_no_vin_passthrough():
    listing = _make_listing(year=2019, make="Ford", model="F-150")

    with (
        patch("app.utils.ingestion.resolve_vin", new=AsyncMock(return_value=None)),
        patch("app.utils.ingestion.normalise_images", new=AsyncMock(return_value=[])),
    ):
        enriched, images = await prepare_listing(listing)

    assert enriched.year == 2019
    assert enriched.make == "Ford"
    assert enriched.model == "F-150"


@pytest.mark.asyncio
async def test_prepare_listing_merges_document_texts():
    listing = _make_listing(
        history_report_text="User notes",
        history_document_texts=["[Document: report.pdf]\nService history here"],
    )

    with (
        patch("app.utils.ingestion.resolve_vin", new=AsyncMock(return_value=None)),
        patch("app.utils.ingestion.normalise_images", new=AsyncMock(return_value=[])),
    ):
        enriched, _ = await prepare_listing(listing)

    assert enriched.history_report_text is not None
    assert "[User notes]" in enriched.history_report_text
    assert "[Document: report.pdf]" in enriched.history_report_text


@pytest.mark.asyncio
async def test_prepare_listing_no_documents_preserves_report_text():
    listing = _make_listing(history_report_text="Original notes")

    with (
        patch("app.utils.ingestion.resolve_vin", new=AsyncMock(return_value=None)),
        patch("app.utils.ingestion.normalise_images", new=AsyncMock(return_value=[])),
    ):
        enriched, _ = await prepare_listing(listing)

    assert enriched.history_report_text == "Original notes"
