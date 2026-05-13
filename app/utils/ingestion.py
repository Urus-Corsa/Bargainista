"""Ingestion utilities — normalise raw user input before agents see it.

Two responsibilities:
  1. Image normalisation — convert image URLs and uploaded base64 strings into
     a single list of base64-encoded strings so the Vision agent has one input format.
  2. VIN resolution — if the user provided a VIN, call the MCP server's
     get_vehicle_specs tool to populate year, make, and model automatically.

Nothing here touches the LLM or the database. This runs before any agent starts.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from app.models.schemas import ListingInput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Image normalisation
# ---------------------------------------------------------------------------


async def fetch_image_as_base64(url: str, client: httpx.AsyncClient) -> str | None:
    """Fetch a remote image and return it as a base64 string.

    Returns None if the fetch fails so the caller can skip bad URLs rather
    than crashing the whole ingestion step.
    """
    try:
        response = await client.get(str(url), timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to fetch image %s: %s", url, exc)
        return None


async def normalise_images(listing: ListingInput) -> list[str]:
    """Return all images as a flat list of base64 strings.

    Fetches image_urls concurrently and merges with any already-base64
    image_base64 uploads. Order: URL-fetched images first, then uploads.
    Skips URLs that fail to fetch rather than raising.
    """
    fetched: list[str] = []

    if listing.image_urls:
        async with httpx.AsyncClient() as client:
            tasks = [fetch_image_as_base64(str(url), client) for url in listing.image_urls]
            results = await asyncio.gather(*tasks)
            fetched = [r for r in results if r is not None]

        if len(fetched) < len(listing.image_urls):
            logger.warning(
                "%d of %d image URLs failed to fetch",
                len(listing.image_urls) - len(fetched),
                len(listing.image_urls),
            )

    return fetched + list(listing.image_base64)


# ---------------------------------------------------------------------------
# VIN resolution via MCP server
# ---------------------------------------------------------------------------


async def resolve_vin(listing: ListingInput) -> dict | None:
    """Call the MCP server's get_vehicle_specs tool to populate vehicle identity.

    Returns a dict with keys: year, make, model, fuel_type, vehicle_class.
    Returns None if no VIN is present or the MCP call fails.

    The MCP server (app/mcp/vehicle_data.py) must be running before this is called.
    Connection details come from settings (MCP_SERVER_URL).
    """
    if not listing.vin:
        return None

    # Import here to avoid circular imports once the MCP client module exists.
    # TODO: replace this stub with the real MCP client call once
    #       app/mcp/vehicle_data.py is implemented (next step in Phase 3).
    try:
        from app.mcp.client import call_tool  # noqa: PLC0415
        specs = await call_tool("get_vehicle_specs", {"vin": listing.vin})
        return specs
    except ImportError:
        logger.warning(
            "MCP client not yet available — VIN %s not resolved. "
            "Ensure app/mcp/client.py is implemented.",
            listing.vin,
        )
        return None
    except Exception as exc:
        logger.warning("VIN resolution failed for %s: %s", listing.vin, exc)
        return None


# ---------------------------------------------------------------------------
# Top-level ingestion entry point
# ---------------------------------------------------------------------------


async def prepare_listing(listing: ListingInput) -> tuple[ListingInput, list[str]]:
    """Normalise a raw ListingInput and return it alongside the prepared image list.

    Returns:
        (enriched_listing, base64_images)

        enriched_listing — original listing with year/make/model populated if VIN
                           was provided and MCP resolution succeeded.
        base64_images    — flat list of base64-encoded image strings ready for
                           the Vision agent. May be empty if all fetches failed.

    This function is the single entry point called by the Celery task before
    handing off to the LangGraph orchestrator.
    """
    images, vin_specs = await asyncio.gather(
        normalise_images(listing),
        resolve_vin(listing),
    )

    enriched = listing
    if vin_specs:
        enriched = listing.model_copy(
            update={
                "year": vin_specs.get("year") or listing.year,
                "make": vin_specs.get("make") or listing.make,
                "model": vin_specs.get("model") or listing.model,
                "trim": vin_specs.get("trim") or listing.trim,
            }
        )
        logger.info(
            "VIN %s resolved to %s %s %s (trim: %s)",
            listing.vin,
            enriched.year,
            enriched.make,
            enriched.model,
            enriched.trim or "not available",
        )

    if not images:
        logger.warning(
            "No images available after normalisation for listing (vin=%s, make=%s). "
            "Vision agent will rely on user_damage_notes only.",
            listing.vin,
            listing.make,
        )

    return enriched, images
