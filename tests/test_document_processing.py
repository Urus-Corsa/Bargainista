"""Unit tests for app/utils/document_processing.py.

All external I/O (pdfplumber, Anthropic client) is mocked. Tests verify
the routing logic and error handling, not the accuracy of OCR output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.utils.document_processing import process_document


def _fake_pdf_bytes() -> bytes:
    """Minimal valid PDF bytes (single empty page)."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"


# ---------------------------------------------------------------------------
# Unsupported extension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_extension_returns_error():
    result = await process_document("report.docx", b"some content")
    assert result["success"] is False
    assert result["format"] == "unknown"
    assert result["text"] == ""
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_no_extension_returns_error():
    result = await process_document("report", b"some content")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# PDF — pdfplumber happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_text_extraction_via_pdfplumber():
    extracted = "VIN: 1HGBH41J 2019 Honda Civic Oil change 45000 miles"

    # _process_pdf has a deferred `import pdfplumber` inside the function body.
    # Patching the internal coroutine is simpler and avoids the deferred-import problem.
    with patch(
        "app.utils.document_processing._process_pdf",
        new=AsyncMock(return_value=extracted),
    ):
        result = await process_document("service.pdf", _fake_pdf_bytes())

    assert result["success"] is True
    assert result["format"] == "pdf"
    assert extracted in result["text"]
    assert result["error"] is None


# ---------------------------------------------------------------------------
# PDF — empty pdfplumber output falls through to Haiku
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_empty_text_falls_back_to_haiku():
    haiku_text = "Extracted via Haiku: accident in 2021"

    with (
        patch(
            "app.utils.document_processing._process_pdf",
            new=AsyncMock(return_value=""),  # empty → triggers fallback
        ),
        patch(
            "app.utils.document_processing._extract_via_haiku",
            new=AsyncMock(return_value=haiku_text),
        ),
    ):
        result = await process_document("scanned.pdf", _fake_pdf_bytes())

    assert result["success"] is True
    assert result["text"] == haiku_text


@pytest.mark.asyncio
async def test_pdf_pdfplumber_raises_falls_back_to_haiku():
    haiku_text = "Haiku fallback text"

    with (
        patch(
            "app.utils.document_processing._process_pdf",
            new=AsyncMock(side_effect=Exception("corrupt PDF")),
        ),
        patch(
            "app.utils.document_processing._extract_via_haiku",
            new=AsyncMock(return_value=haiku_text),
        ),
    ):
        result = await process_document("scanned.pdf", _fake_pdf_bytes())

    assert result["success"] is True
    assert result["text"] == haiku_text


# ---------------------------------------------------------------------------
# Image — goes directly to Haiku
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["photo.jpg", "photo.jpeg", "photo.png", "photo.webp"])
async def test_image_uses_haiku_directly(filename: str):
    haiku_text = "Vehicle history extracted from image"

    with patch(
        "app.utils.document_processing._extract_via_haiku",
        new=AsyncMock(return_value=haiku_text),
    ):
        result = await process_document(filename, b"fake image bytes")

    assert result["success"] is True
    assert result["format"] == "image"
    assert result["text"] == haiku_text


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_haiku_exception_returns_error_result():
    with patch(
        "app.utils.document_processing._extract_via_haiku",
        new=AsyncMock(side_effect=RuntimeError("API timeout")),
    ):
        result = await process_document("photo.jpg", b"fake image bytes")

    assert result["success"] is False
    assert result["text"] == ""
    assert result["error"] is not None
    assert "API timeout" in result["error"]


@pytest.mark.asyncio
async def test_haiku_exception_on_pdf_returns_error():
    # Both pdfplumber and Haiku fail → error result
    with (
        patch(
            "app.utils.document_processing._process_pdf",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "app.utils.document_processing._extract_via_haiku",
            new=AsyncMock(side_effect=RuntimeError("API timeout on PDF")),
        ),
    ):
        result = await process_document("broken.pdf", _fake_pdf_bytes())

    assert result["success"] is False
    assert result["text"] == ""
