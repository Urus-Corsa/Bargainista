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
