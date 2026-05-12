"""MCP client — thin wrapper for agents to call vehicle-data tools.

Agents call call_tool() directly. This module handles the HTTP connection
to the MCP server, the retry-once logic, and the soft-fail contract:
if the server is unavailable, call_tool() returns None rather than raising.

The MCP server URL is read from settings (MCP_SERVER_URL).
Default: http://mcp:8001 (Docker internal network service name).
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MCP_TOOL_ENDPOINT = "{base}/mcp/v1/tools/{tool}"


async def call_tool(tool_name: str, arguments: dict) -> dict | None:
    """Call an MCP tool and return the parsed result dict.

    Retries once on failure (handles transient NHTSA or network blips).
    Returns None on second failure — callers must handle None as degraded mode.

    Args:
        tool_name:  One of get_vehicle_specs | get_vehicle_recalls | get_safety_ratings
        arguments:  Tool input dict matching the tool's inputSchema

    Returns:
        Parsed result dict from the tool, or None if the call failed after retry.
    """
    url = _MCP_TOOL_ENDPOINT.format(
        base=settings.mcp_server_url.rstrip("/"),
        tool=tool_name,
    )

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(url, json={"arguments": arguments})
                response.raise_for_status()
                payload = response.json()

            # MCP tool responses arrive as a list of content blocks.
            # We only use TextContent blocks and parse the first one.
            contents = payload.get("content", [])
            for block in contents:
                if block.get("type") == "text":
                    result = json.loads(block["text"])
                    if "error" in result:
                        logger.warning("MCP tool %s returned error: %s", tool_name, result["error"])
                        return None
                    return result

            logger.warning("MCP tool %s returned no text content", tool_name)
            return None

        except Exception as exc:
            if attempt == 0:
                logger.warning(
                    "MCP tool %s failed (attempt 1), retrying: %s", tool_name, exc
                )
            else:
                logger.warning(
                    "MCP tool %s failed after retry — running in degraded mode: %s",
                    tool_name,
                    exc,
                )
                return None

    return None  # unreachable but satisfies type checker
