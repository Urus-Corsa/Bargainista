"""MCP server — vehicle data tools backed by the free NHTSA APIs.

Exposes three tools:
  get_vehicle_specs(vin)                     → NHTSA vPIC: year, make, model, fuel, class
  get_vehicle_recalls(make, model, year)     → NHTSA recalls: open safety recalls
  get_safety_ratings(make, model, year)      → NHTSA NCAP: crash test ratings

Running modes:
  Standalone (HTTP, for Docker / Claude Desktop):
      python -m app.mcp.vehicle_data

  In-process (used by tests and the MCP client module):
      from app.mcp.vehicle_data import create_server
      server = create_server()

NHTSA API base URLs:
  vPIC:    https://vpic.nhtsa.dot.gov/api/
  Recalls: https://api.nhtsa.gov/recalls/recallsByVehicle
  NCAP:    https://api.nhtsa.gov/SafetyRatings
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Any

import httpx
import redis.asyncio as aioredis
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NHTSA endpoints — all free, no API key
# ---------------------------------------------------------------------------
# RepairPal partner API — requires REPAIRPAL_API_KEY env var.
# If the key is absent the tool falls back to _STATIC_REPAIR_COSTS.
# RepairPal partner access: https://repairpal.com/partners
# ---------------------------------------------------------------------------

REPAIRPAL_ESTIMATE_URL = (
    "https://api.repairpal.com/v1/repair_price"
    "?api_key={api_key}&make={make}&model={model}&year={year}"
    "&repair_id={repair_id}&zip={zip}"
)

# RepairPal internal repair IDs for common jobs.
# Populated from the RepairPal partner API catalog — extend when full catalog is available.
_REPAIRPAL_REPAIR_IDS: dict[str, int] = {
    "oil change": 2,
    "tire rotation": 5,
    "brake pad replacement": 343,
    "brake rotor replacement": 344,
    "brake pads and rotors": 345,
    "timing belt replacement": 98,
    "timing chain replacement": 99,
    "water pump replacement": 106,
    "alternator replacement": 12,
    "battery replacement": 20,
    "spark plug replacement": 88,
    "transmission service": 101,
    "catalytic converter replacement": 29,
    "strut replacement": 93,
    "control arm replacement": 38,
    "wheel bearing replacement": 110,
    "cv axle replacement": 42,
}

# Static national-average fallback when RepairPal API key is not configured.
# Ranges represent non-dealer shop rates (labor + parts). Source: RepairPal 2023 data.
_STATIC_REPAIR_COSTS: dict[str, tuple[int, int]] = {
    # Brakes
    "brake pad replacement": (150, 350),
    "brake pads": (150, 350),
    "brake rotor replacement": (300, 600),
    "brake rotors": (300, 600),
    "brake pads and rotors": (400, 900),
    "brake caliper replacement": (300, 700),
    "brake fluid flush": (80, 150),
    "brake line repair": (200, 600),
    "brake lines": (200, 600),
    # Engine — routine
    "oil change": (60, 130),
    "oil and filter": (60, 130),
    "spark plug replacement": (200, 500),
    "spark plugs": (200, 500),
    "timing belt replacement": (500, 1_000),
    "timing belt": (500, 1_000),
    "timing chain replacement": (1_000, 2_500),
    "timing chain": (1_000, 2_500),
    "valve cover gasket": (200, 500),
    "head gasket replacement": (1_200, 2_800),
    "head gasket": (1_200, 2_800),
    "water pump replacement": (400, 900),
    "water pump": (400, 900),
    "thermostat replacement": (200, 400),
    "oxygen sensor replacement": (200, 450),
    "catalytic converter replacement": (1_000, 3_000),
    "catalytic converter": (1_000, 3_000),
    "fuel pump replacement": (400, 900),
    "fuel pump": (400, 900),
    "alternator replacement": (400, 800),
    "alternator": (400, 800),
    "starter replacement": (300, 650),
    "battery replacement": (150, 350),
    # Transmission
    "transmission service": (150, 350),
    "transmission fluid": (100, 250),
    "transmission replacement": (2_000, 5_000),
    "clutch replacement": (800, 1_800),
    "clutch": (800, 1_800),
    # Suspension
    "strut replacement": (600, 1_200),
    "struts": (600, 1_200),
    "shock absorber replacement": (400, 900),
    "shocks": (400, 900),
    "control arm replacement": (400, 900),
    "control arm": (400, 900),
    "tie rod replacement": (200, 500),
    "tie rod": (200, 500),
    "ball joint replacement": (300, 700),
    "ball joint": (300, 700),
    "wheel bearing replacement": (300, 700),
    "wheel bearing": (300, 700),
    "cv axle replacement": (300, 700),
    "cv axle": (300, 700),
    "sway bar link": (150, 350),
    # Tires
    "tire rotation": (20, 50),
    "tire replacement": (500, 1_000),
    "tires": (500, 1_100),
    "wheel alignment": (80, 180),
    # HVAC
    "ac compressor replacement": (600, 1_400),
    "ac recharge": (150, 300),
    # Steering
    "power steering pump replacement": (400, 800),
    "power steering pump": (400, 800),
    # Exhaust
    "exhaust repair": (300, 800),
    "exhaust system": (500, 1_500),
    "muffler replacement": (200, 500),
    # Cooling
    "radiator replacement": (400, 900),
    "radiator": (400, 900),
    "coolant flush": (100, 200),
    # Filters
    "air filter": (30, 80),
    "cabin air filter": (30, 80),
    "fuel filter": (100, 200),
    # Rust on safety/structural components
    "brake line rust": (400, 1_000),
    "fuel line rust": (500, 1_200),
    "exhaust rust": (200, 600),
    "subframe rust": (1_000, 4_000),
    "frame rust": (1_000, 4_000),
    "suspension rust": (400, 1_500),
}

NHTSA_VIN_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
NHTSA_RECALLS_URL = (
    "https://api.nhtsa.gov/recalls/recallsByVehicle"
    "?make={make}&model={model}&modelYear={year}"
)
NHTSA_NCAP_URL = (
    "https://api.nhtsa.gov/SafetyRatings/modelyear/{year}/make/{make}/model/{model}"
)

CACHE_TTL = 86_400  # 24 hours — recall data doesn't change day-to-day


# ---------------------------------------------------------------------------
# Redis cache helpers
# ---------------------------------------------------------------------------


def _cache_key(tool: str, args: dict) -> str:
    payload = json.dumps(args, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"mcp:{tool}:{digest}"


async def _get_redis() -> aioredis.Redis | None:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        client = aioredis.from_url(url, decode_responses=True)
        await client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis unavailable, caching disabled: %s", exc)
        return None


async def _cached_get(
    tool: str, args: dict, fetch_fn, redis: aioredis.Redis | None
) -> dict:
    """Return cached result if present, otherwise call fetch_fn and cache the result."""
    if redis:
        key = _cache_key(tool, args)
        cached = await redis.get(key)
        if cached:
            logger.debug("Cache hit: %s", key)
            return json.loads(cached)

    result = await fetch_fn()

    if redis and result:
        key = _cache_key(tool, args)
        await redis.set(key, json.dumps(result), ex=CACHE_TTL)

    return result


# ---------------------------------------------------------------------------
# NHTSA fetch functions
# ---------------------------------------------------------------------------


async def _fetch_vehicle_specs(vin: str) -> dict:
    url = NHTSA_VIN_URL.format(vin=vin)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    results = {r["Variable"]: r["Value"] for r in data.get("Results", [])}

    def get(key: str) -> str | None:
        v = results.get(key)
        return v if v and v != "Not Applicable" else None

    return {
        "vin": vin,
        "year": get("Model Year"),
        "make": get("Make"),
        "model": get("Model"),
        "trim": get("Trim"),
        "engine_displacement_l": get("Displacement (L)"),
        "fuel_type": get("Fuel Type - Primary"),
        "vehicle_class": get("Body Class"),
        "drive_type": get("Drive Type"),
        "manufacturer": get("Manufacturer Name"),
    }


async def _fetch_vehicle_recalls(make: str, model: str, year: int) -> dict:
    url = NHTSA_RECALLS_URL.format(make=make, model=model, year=year)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    recalls = []
    for r in data.get("results", []):
        recalls.append({
            "recall_id": r.get("NHTSACampaignNumber"),
            "component": r.get("Component"),
            "summary": r.get("Summary"),
            "consequence": r.get("Conséquence") or r.get("Consequence"),
            "remedy": r.get("Remedy"),
            "report_date": r.get("ReportReceivedDate"),
        })

    return {
        "make": make,
        "model": model,
        "year": year,
        "recall_count": len(recalls),
        "recalls": recalls,
    }


def _normalize_repair_type(repair_type: str) -> str:
    return repair_type.lower().strip().replace("-", " ").replace("_", " ")


def _best_static_match(repair_type: str) -> tuple[int, int] | None:
    """Find the closest entry in _STATIC_REPAIR_COSTS using word overlap."""
    normalized = _normalize_repair_type(repair_type)
    if normalized in _STATIC_REPAIR_COSTS:
        return _STATIC_REPAIR_COSTS[normalized]

    query_words = set(normalized.split())
    best_score = 0
    best_costs: tuple[int, int] | None = None
    for key, costs in _STATIC_REPAIR_COSTS.items():
        overlap = len(query_words & set(key.split()))
        if overlap > best_score:
            best_score = overlap
            best_costs = costs

    return best_costs if best_score >= 1 else None


async def _fetch_repair_estimate(
    make: str, model: str, year: int, repair_type: str, zip_code: str | None
) -> dict:
    """Return a repair cost estimate for the given vehicle and repair type.

    Priority:
      1. RepairPal API if REPAIRPAL_API_KEY is configured and repair_id is known.
      2. Static national-average lookup from _STATIC_REPAIR_COSTS (word-matched).
      3. Unavailable response if neither source has data.

    RepairPal partner API access: https://repairpal.com/partners
    The repair_id mapping in _REPAIRPAL_REPAIR_IDS covers common jobs; the full
    catalog requires a partner account and is extended by adding entries there.
    """
    api_key = os.getenv("REPAIRPAL_API_KEY", "")
    normalized = _normalize_repair_type(repair_type)

    if api_key:
        repair_id = _REPAIRPAL_REPAIR_IDS.get(normalized)
        if repair_id:
            try:
                url = REPAIRPAL_ESTIMATE_URL.format(
                    api_key=api_key,
                    make=make,
                    model=model,
                    year=year,
                    repair_id=repair_id,
                    zip=zip_code or "10001",
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()
                return {
                    "available": True,
                    "source": "repairpal",
                    "make": make,
                    "model": model,
                    "year": year,
                    "repair_type": repair_type,
                    "cost_low": data.get("low"),
                    "cost_high": data.get("high"),
                    "zip_code": zip_code,
                }
            except Exception as exc:
                logger.warning("RepairPal API failed for '%s': %s", repair_type, exc)
                # fall through to static lookup

    costs = _best_static_match(repair_type)
    if costs:
        return {
            "available": True,
            "source": "static_lookup",
            "make": make,
            "model": model,
            "year": year,
            "repair_type": repair_type,
            "cost_low": costs[0],
            "cost_high": costs[1],
            "note": (
                "Static national average. "
                "Configure REPAIRPAL_API_KEY for location-specific estimates."
            ),
        }

    return {
        "available": False,
        "reason": f"No estimate available for repair type: {repair_type!r}",
        "make": make,
        "model": model,
        "year": year,
        "repair_type": repair_type,
    }


async def _fetch_safety_ratings(make: str, model: str, year: int) -> dict:
    url = NHTSA_NCAP_URL.format(year=year, make=make, model=model)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    results = data.get("Results", [])
    if not results:
        return {"make": make, "model": model, "year": year, "ratings": []}

    ratings = []
    for r in results:
        ratings.append({
            "vehicle_description": r.get("VehicleDescription"),
            "overall_rating": r.get("OverallRating"),
            "frontal_crash": r.get("OverallFrontCrashRating"),
            "side_crash": r.get("OverallSideCrashRating"),
            "rollover": r.get("RolloverRating"),
        })

    return {"make": make, "model": model, "year": year, "ratings": ratings}


# ---------------------------------------------------------------------------
# MCP server factory
# ---------------------------------------------------------------------------


def create_server() -> Server:
    server = Server("vehicle-data")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_vehicle_specs",
                description=(
                    "Decode a 17-character VIN using the NHTSA vPIC API. "
                    "Returns year, make, model, trim, engine displacement, "
                    "fuel type, body class, and drive type."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vin": {
                            "type": "string",
                            "description": "17-character Vehicle Identification Number",
                            "minLength": 17,
                            "maxLength": 17,
                        }
                    },
                    "required": ["vin"],
                },
            ),
            Tool(
                name="get_vehicle_recalls",
                description=(
                    "Fetch open safety recalls for a vehicle from the NHTSA recalls API. "
                    "Returns recall count, affected component, summary, consequence, "
                    "and remedy for each recall."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "make": {"type": "string", "description": "Vehicle manufacturer"},
                        "model": {"type": "string", "description": "Vehicle model name"},
                        "year": {"type": "integer", "description": "Model year"},
                    },
                    "required": ["make", "model", "year"],
                },
            ),
            Tool(
                name="get_safety_ratings",
                description=(
                    "Fetch NHTSA NCAP crash test safety ratings for a vehicle. "
                    "Returns overall, frontal crash, side crash, and rollover ratings "
                    "on a 1–5 star scale."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "make": {"type": "string"},
                        "model": {"type": "string"},
                        "year": {"type": "integer"},
                    },
                    "required": ["make", "model", "year"],
                },
            ),
            Tool(
                name="get_repair_estimate",
                description=(
                    "Estimate repair cost for a specific repair type on a given vehicle. "
                    "Returns cost_low and cost_high in USD at non-dealer shop rates. "
                    "Uses the RepairPal API when REPAIRPAL_API_KEY is configured; "
                    "otherwise returns static national-average estimates. "
                    "Accepts free-form repair_type strings — matched to the closest known type."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "make": {"type": "string", "description": "Vehicle manufacturer"},
                        "model": {"type": "string", "description": "Vehicle model name"},
                        "year": {"type": "integer", "description": "Model year"},
                        "repair_type": {
                            "type": "string",
                            "description": (
                                "Free-form description of the repair. "
                                "e.g. 'brake pad replacement', 'timing belt', 'struts'"
                            ),
                        },
                        "zip_code": {
                            "type": "string",
                            "description": (
                                "5-digit US zip code for location-specific pricing. "
                                "Optional — omit to receive national-average estimates."
                            ),
                        },
                    },
                    "required": ["make", "model", "year", "repair_type"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        redis = await _get_redis()

        try:
            if name == "get_vehicle_specs":
                vin = arguments["vin"]
                result = await _cached_get(
                    "get_vehicle_specs",
                    {"vin": vin},
                    lambda: _fetch_vehicle_specs(vin),
                    redis,
                )

            elif name == "get_vehicle_recalls":
                make = arguments["make"]
                model = arguments["model"]
                year = int(arguments["year"])
                result = await _cached_get(
                    "get_vehicle_recalls",
                    {"make": make, "model": model, "year": year},
                    lambda: _fetch_vehicle_recalls(make, model, year),
                    redis,
                )

            elif name == "get_safety_ratings":
                make = arguments["make"]
                model = arguments["model"]
                year = int(arguments["year"])
                result = await _cached_get(
                    "get_safety_ratings",
                    {"make": make, "model": model, "year": year},
                    lambda: _fetch_safety_ratings(make, model, year),
                    redis,
                )

            elif name == "get_repair_estimate":
                make = arguments["make"]
                model = arguments["model"]
                year = int(arguments["year"])
                repair_type = arguments["repair_type"]
                zip_code = arguments.get("zip_code")
                result = await _cached_get(
                    "get_repair_estimate",
                    {"make": make, "model": model, "year": year,
                     "repair_type": repair_type, "zip_code": zip_code},
                    lambda: _fetch_repair_estimate(make, model, year, repair_type, zip_code),
                    redis,
                )

            else:
                result = {"error": f"Unknown tool: {name}"}

        except httpx.HTTPStatusError as exc:
            logger.warning("NHTSA API error for tool %s: %s", name, exc)
            result = {"error": f"NHTSA API returned {exc.response.status_code}"}
        except Exception as exc:
            logger.warning("Tool %s failed: %s", name, exc)
            result = {"error": str(exc)}
        finally:
            if redis:
                await redis.aclose()

        return [TextContent(type="text", text=json.dumps(result))]

    return server


# ---------------------------------------------------------------------------
# Standalone HTTP entrypoint
# ---------------------------------------------------------------------------


async def _serve() -> None:
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8001"))

    server = create_server()
    transport = StreamableHTTPServerTransport(host=host, port=port)

    logger.info("MCP vehicle-data server starting on %s:%s", host, port)
    await server.run(transport)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
