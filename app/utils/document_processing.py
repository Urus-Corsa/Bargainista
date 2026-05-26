"""Document processing pipeline — extract text from uploaded PDF and image files.

Entry point: process_document(filename, content) -> ProcessedDocument
"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import TypedDict

from app.core.llm import get_anthropic_client

# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

_SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}

_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

_HAIKU_PROMPT = (
    "Extract all vehicle history information from this document: "
    "ownership records, accidents, repairs, mileage readings, title events, "
    "odometer statements. Return only the extracted text."
)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"


class ProcessedDocument(TypedDict):
    filename: str
    format: str        # "pdf" | "image"
    text: str          # empty string on failure
    success: bool
    error: str | None  # None on success


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extension(filename: str) -> str:
    lower = filename.lower()
    for ext in _SUPPORTED_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    return ""


async def _extract_via_haiku(content: bytes, media_type: str) -> str:
    client = get_anthropic_client()
    b64_data = base64.standard_b64encode(content).decode("utf-8")
    # PDFs use the native document source type; images use the image source type.
    content_block: dict = (
        {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }
        if media_type == "application/pdf"
        else {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }
    )
    response = await client.messages.create(  # type: ignore[call-overload]
        model=_HAIKU_MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,  # type: ignore[list-item]
                    {"type": "text", "text": _HAIKU_PROMPT},
                ],
            }
        ],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_blocks).strip()


async def _process_pdf(content: bytes) -> str:
    import pdfplumber  # noqa: PLC0415 — deferred so startup doesn't fail if missing

    text_parts: list[str] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts).strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def process_document(filename: str, content: bytes) -> ProcessedDocument:
    ext = _extension(filename)

    if ext not in _SUPPORTED_EXTENSIONS:
        return ProcessedDocument(
            filename=filename,
            format="unknown",
            text="",
            success=False,
            error=f"Unsupported file type: {ext or 'no extension'}",
        )

    is_pdf = ext == ".pdf"
    doc_format = "pdf" if is_pdf else "image"

    try:
        if is_pdf:
            try:
                extracted = await _process_pdf(content)
            except Exception:
                extracted = ""

            if extracted:
                return ProcessedDocument(
                    filename=filename,
                    format=doc_format,
                    text=extracted,
                    success=True,
                    error=None,
                )

            # pdfplumber returned empty (scanned PDF) — fall through to Haiku using native PDF type
            extracted = await _extract_via_haiku(content, "application/pdf")
        else:
            media_type = _MEDIA_TYPES[ext]
            extracted = await _extract_via_haiku(content, media_type)

        return ProcessedDocument(
            filename=filename,
            format=doc_format,
            text=extracted,
            success=True,
            error=None,
        )

    except Exception as exc:
        return ProcessedDocument(
            filename=filename,
            format=doc_format,
            text="",
            success=False,
            error=str(exc)[:200],
        )
