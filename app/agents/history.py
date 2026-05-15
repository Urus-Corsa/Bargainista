"""History agent — listing text analysis and NHTSA recall augmentation.

Execution flow:
    1. Pre-fetch NHTSA recalls and safety ratings via MCP (explicit Python calls).
       Python records which fetches succeeded in data_sources_available.
    2. Build a single LLM prompt containing all available data:
       listing_description, history_report_text, formatted recall data,
       safety ratings, and vehicle context.
    3. Single LLM call (Sonnet) with tool_use to enforce HistoryAgentResult structure.
    4. Post-process: set contributing_sources and confidence on repair_mentions
       that were inferred (history_inference) vs. explicitly mentioned (listing_mention).
       Python sets data_sources_available — the LLM never produces this field.

Entry point: run(listing) -> HistoryAgentResult
"""

from __future__ import annotations

import json
import logging

import anthropic

from app.core.config import settings
from app.core.llm import get_anthropic_client
from app.mcp.client import call_tool
from app.models.schemas import (
    ConfidenceLevel,
    DamageSeverity,
    EstimateSource,
    HistoryAgentResult,
    ListingInput,
    RepairCategory,
    RepairEstimate,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

# estimate_source is intentionally absent — Python sets contributing_sources
# and confidence after deserialization (Decision 16).
_HISTORY_TOOL: dict = {
    "name": "history_analysis",
    "description": (
        "Produce a structured risk assessment of a used vehicle listing based on "
        "the listing text, history report, and NHTSA recall data provided."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "risk_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "Overall history risk score. Start at 5 (neutral). "
                    "10 = single owner, complete documented service history, no accidents, clean title. "
                    "1 = multiple serious red flags (salvage title, frame damage, odometer rollback). "
                    "Open recalls do NOT reduce this score — they are manufacturer issues. "
                    "Salvage or rebuilt title caps the score at 3."
                ),
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific, concrete concerns. Quote source text where possible. "
                    "Each open NHTSA recall is a separate red flag entry. "
                    "Format: '<concern>. Source: <listing/history report/NHTSA recall>'."
                ),
            },
            "mileage_consistent": {
                "type": "boolean",
                "description": (
                    "True if the stated mileage is plausible given vehicle year, asking price, "
                    "and any signals in the description (e.g. 'highway miles', 'daily driver'). "
                    "False if mileage appears suspiciously low or high relative to these signals."
                ),
            },
            "ownership_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Notes on number of owners, lease vs. private, fleet/rental/taxi use, "
                    "geographic history (rust belt exposure), or other provenance signals."
                ),
            },
            "accident_mentions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Accident, collision, or damage repair mentions extracted verbatim or "
                    "closely paraphrased from the listing or history report. "
                    "Include severity if stated."
                ),
            },
            "title_concerns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Salvage, rebuilt, lemon law buyback, odometer rollback, flood damage, "
                    "or any other title status concerns. Empty array if none found."
                ),
            },
            "repair_mentions": {
                "type": "array",
                "description": (
                    "Mechanical or maintenance items extracted from the listing text or "
                    "inferred from service history gaps. "
                    "Do NOT include cosmetic items (paint, dents, trim) — those belong to "
                    "the Vision agent. "
                    "For inferred items (not explicitly mentioned), set "
                    "is_inferred=true and explain the reasoning in inference_reasoning."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "component": {
                            "type": "string",
                            "description": "e.g. 'brake pads', 'timing belt', 'tires'",
                        },
                        "damage_type": {
                            "type": "string",
                            "description": "e.g. 'worn', 'overdue service', 'not mentioned since high mileage'",
                        },
                        "repair_category": {
                            "type": "string",
                            "enum": ["mechanical", "maintenance"],
                            "description": "mechanical = brakes/suspension/engine/transmission; maintenance = oil/tires/filters/scheduled service",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["none", "minor", "moderate", "severe"],
                        },
                        "is_inferred": {
                            "type": "boolean",
                            "description": (
                                "True if this item was deduced from service history gaps "
                                "rather than explicitly mentioned in the text."
                            ),
                        },
                        "inference_reasoning": {
                            "type": "string",
                            "description": (
                                "Required. Plain-language explanation of why this item was flagged. "
                                "For explicit mentions: quote the relevant text. "
                                "For inferred items: explain the evidence chain "
                                "(e.g. 'Brake service last recorded at 62k miles per history report; "
                                "current odometer 118k — typical interval is 50–70k miles')."
                            ),
                        },
                        "estimated_cost_low": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Low end of repair/service cost in USD at a non-dealer shop",
                        },
                        "estimated_cost_high": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "High end of repair/service cost in USD",
                        },
                    },
                    "required": [
                        "component", "damage_type", "repair_category", "severity",
                        "is_inferred", "inference_reasoning",
                        "estimated_cost_low", "estimated_cost_high",
                    ],
                },
            },
            "summary": {
                "type": "string",
                "description": (
                    "2–4 sentences. Plain-language history assessment for the synthesiser. "
                    "Lead with the most important finding. State whether recall data was "
                    "available and how it affected the assessment."
                ),
            },
        },
        "required": [
            "risk_score", "red_flags", "mileage_consistent", "ownership_signals",
            "accident_mentions", "title_concerns", "repair_mentions", "summary",
        ],
    },
}

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a vehicle history analyst reviewing a used car listing on behalf of a buyer.

Your job is to assess the vehicle's history — based strictly on the information \
provided. Do not invent facts not present in the listing text, history report, \
or NHTSA data.

SCORING PHILOSOPHY
risk_score reflects history quality, not vehicle design quality. Start at 5 \
(neutral — history is unknown or unremarkable). Move upward when the history is \
clean and well-documented. Move downward when there are concrete red flags in \
the record.

RISK SCORE GUIDELINES

Positive adjustments (move score up):
  Single owner, complete documented service history          +2
  No accidents ever recorded (confirmed, not just claimed)   +1
  Clean title, all open recalls previously addressed         +1
  Mileage consistent and plausible for age and stated use    +0.5
  Verifiably low mileage matching claimed use pattern        +0.5

Negative adjustments (move score down):
  Minor accident or cosmetic damage recorded                 -1
  Major accident or structural/frame damage                  -2
  Salvage or rebuilt title                                    score capped at 3
  Lemon law buyback                                          score capped at 3
  Odometer rollback indicators                               -2
  Mileage inconsistent with year, price, or description      -1
  Fleet, rental, or taxi use                                 -1
  Multiple owners in a short period (e.g. 3 in 2 years)     -1

LOCATION-BASED RUST RISK
States with regular winter snowfall and heavy road salt usage (Great Lakes, \
Northeast, mid-Atlantic, upper Midwest) significantly increase the probability \
of corrosion. The penalty depends on which components are affected — rust is \
not a uniform concern.

  No rust inspection mentioned, car from a high-salt state    -0.5 (baseline uncertainty)
  Rust on body panels or wheel wells                          note only — Vision agent
                                                              assesses cosmetics from photos
  Rust on exhaust system                                      -0.5
  Rust on suspension components (control arms, sway bars,
    struts, springs)                                          -1.5
  Rust on brake lines or fuel lines                           -2.5 (safety-critical)
  Rust on subframe or frame                                   -3.0 (structural integrity)

If the listing or history report shows the vehicle was registered or driven in a \
high-salt state — even if the current listing is in a dry state — the rust risk \
follows the car. Do not reduce the concern based on current location alone. \
Add rust-related items to repair_mentions with the appropriate severity and \
estimated remediation cost.

NHTSA RECALLS
Surface all open recalls in red_flags. Note that recalls are typically resolved \
free of charge at the franchised dealer. Do NOT adjust risk_score for open \
recalls — they are a manufacturer issue, not a history issue. If recalls have \
already been addressed, note that as a positive signal in ownership_signals.

NHTSA SAFETY RATINGS
Include safety ratings as informational context in the summary only. \
Do not use safety ratings as a risk_score input — they reflect vehicle design, \
not listing history or value.

REPAIR MENTIONS
- Extract only mechanical and maintenance items: brakes, suspension, engine, \
  transmission, tires, oil, filters, timing belts, and rust-affected mechanical \
  components (brake lines, fuel lines, subframe).
- Do NOT include cosmetic items (paint, dents, trim, glass, body panel rust) — \
  those are assessed independently by the Vision agent from photos.
- Explicitly mentioned items: is_inferred=false, quote the source text.
- Service history gap inferences: is_inferred=true, show the mileage math in \
  inference_reasoning.
- Rust on safety or structural components is always a repair_mention regardless \
  of whether it was inferred — include estimated remediation cost.
- Use non-dealer shop rates for cost estimates.

MILEAGE CONSISTENCY
Average is 12,000 miles/year. Assess plausibility against year, price, and \
description signals. Suspiciously low mileage on an old car is as much a \
concern as suspiciously high.

DATA SOURCE DISCIPLINE
Distinguish between what came from the listing, the history report, and NHTSA. \
If a section is absent, state that in the summary — do not fill gaps with \
assumptions or training knowledge.\
"""


def _format_recalls(recalls: dict | None, make: str, model: str, year: int | None) -> str:
    if not recalls:
        return "NHTSA recall data: unavailable (MCP call failed or no VIN provided)"
    results = recalls.get("results", [])
    if not results:
        return f"NHTSA recall data: no open recalls found for {year} {make} {model}"
    lines = [f"NHTSA recall data: {len(results)} open recall(s) for {year} {make} {model}"]
    for r in results[:10]:  # cap at 10 to control token count
        lines.append(
            f"  - [{r.get('NHTSACampaignNumber', 'N/A')}] {r.get('Component', 'Unknown component')}: "
            f"{r.get('Summary', 'No summary available')}"
        )
    return "\n".join(lines)


def _format_safety(safety: dict | None, make: str, model: str, year: int | None) -> str:
    if not safety:
        return "NHTSA safety ratings: unavailable"
    results = safety.get("results", [])
    if not results:
        return f"NHTSA safety ratings: no ratings found for {year} {make} {model}"
    r = results[0]
    return (
        f"NHTSA safety ratings for {year} {make} {model}: "
        f"Overall {r.get('OverallRating', 'N/A')}/5 — "
        f"Frontal crash {r.get('FrontCrashDriversideRating', 'N/A')}/5, "
        f"Side crash {r.get('SideCrashDriversideRating', 'N/A')}/5, "
        f"Rollover {r.get('RolloverRating', 'N/A')}/5"
    )


def _build_user_message(
    listing: ListingInput,
    recalls: dict | None,
    safety: dict | None,
    data_sources: list[str],
) -> str:
    make = listing.make or "Unknown"
    model = listing.model or "Unknown"
    year = listing.year

    sections: list[str] = []

    sections.append(
        f"VEHICLE\n"
        f"  {year} {make} {model}\n"
        f"  Mileage: {listing.mileage:,} miles\n"
        f"  Asking price: ${listing.asking_price:,}\n"
        f"  Location: {listing.location}"
    )

    sections.append(
        _format_recalls(recalls, make, model, year)
    )

    sections.append(
        _format_safety(safety, make, model, year)
    )

    if listing.listing_description:
        sections.append(
            f"LISTING DESCRIPTION\n{listing.listing_description}"
        )
    else:
        sections.append("LISTING DESCRIPTION\n(not provided)")

    if listing.history_report_text:
        sections.append(
            f"HISTORY REPORT (user-provided)\n{listing.history_report_text}"
        )
    else:
        sections.append("HISTORY REPORT\n(not provided)")

    if data_sources:
        sections.append(
            f"DATA SOURCES AVAILABLE: {', '.join(data_sources)}"
        )
    else:
        sections.append(
            "DATA SOURCES AVAILABLE: none — MCP tools unavailable, analysis based on listing text only"
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------


def _build_repair_estimate(item: dict) -> RepairEstimate:
    """Convert the LLM's tool_use repair item dict to a RepairEstimate.

    contributing_sources and confidence are set here by Python — not by the LLM.
    is_inferred drives the source and confidence assignment.
    """
    is_inferred = item.get("is_inferred", False)

    if is_inferred:
        sources = [EstimateSource.history_inference]
        confidence = ConfidenceLevel.not_confident
    else:
        sources = [EstimateSource.listing_mention]
        confidence = ConfidenceLevel.confident

    return RepairEstimate(
        component=item["component"],
        damage_type=item["damage_type"],
        repair_category=RepairCategory(item["repair_category"]),
        contributing_sources=sources,
        confidence=confidence,
        severity=DamageSeverity(item["severity"]),
        inference_reasoning=item["inference_reasoning"],
        estimated_cost_low=item["estimated_cost_low"],
        estimated_cost_high=item["estimated_cost_high"],
    )


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


async def run(listing: ListingInput) -> HistoryAgentResult:
    """Run the History agent.

    Args:
        listing: Enriched ListingInput from the ingestion pipeline.

    Returns:
        HistoryAgentResult with all fields populated.
        data_sources_available is set by Python from actual MCP call results.
        repair_mentions[*].contributing_sources and confidence are set by Python
        based on is_inferred — the LLM never writes these fields.

    Raises:
        anthropic.APIError: if the LLM call fails.
        RuntimeError: if the LLM does not return the expected tool_use block.
    """
    make = (listing.make or "").strip()
    model_name = listing.model or ""
    year = listing.year

    # ------------------------------------------------------------------
    # 1. Pre-fetch MCP data (explicit Python calls — Decision 3 and 14)
    # ------------------------------------------------------------------
    data_sources: list[str] = []

    recalls: dict | None = None
    if make and model_name and year:
        recalls = await call_tool(
            "get_vehicle_recalls",
            {"make": make, "model": model_name, "year": year},
        )
        if recalls:
            data_sources.append("get_vehicle_recalls")
        else:
            logger.warning("History agent: recall fetch failed for %s %s %s", year, make, model_name)

    safety: dict | None = None
    if make and model_name and year:
        safety = await call_tool(
            "get_safety_ratings",
            {"make": make, "model": model_name, "year": year},
        )
        if safety:
            data_sources.append("get_safety_ratings")
        else:
            logger.warning("History agent: safety rating fetch failed for %s %s %s", year, make, model_name)

    logger.info(
        "History agent: data sources available: %s",
        data_sources if data_sources else ["none"],
    )

    # ------------------------------------------------------------------
    # 2. Build prompt
    # ------------------------------------------------------------------
    user_message = _build_user_message(listing, recalls, safety, data_sources)

    # ------------------------------------------------------------------
    # 3. LLM call — Sonnet, tool_use forced (Decision 15, Decision 13)
    # ------------------------------------------------------------------
    client = get_anthropic_client()

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        tools=[_HISTORY_TOOL],  # type: ignore[list-item]
        tool_choice={"type": "tool", "name": "history_analysis"},
        messages=[{"role": "user", "content": user_message}],
    )

    # ------------------------------------------------------------------
    # 4. Parse tool_use response
    # ------------------------------------------------------------------
    raw: dict | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "history_analysis":
            raw = block.input
            break

    if raw is None:
        raise RuntimeError("History agent LLM did not return the expected tool_use block")

    # ------------------------------------------------------------------
    # 5. Post-process repair_mentions
    #    Python sets contributing_sources and confidence (Decision 16).
    # ------------------------------------------------------------------
    repair_mentions = [_build_repair_estimate(item) for item in raw.get("repair_mentions", [])]

    return HistoryAgentResult(
        risk_score=raw["risk_score"],
        red_flags=raw.get("red_flags", []),
        mileage_consistent=raw["mileage_consistent"],
        ownership_signals=raw.get("ownership_signals", []),
        accident_mentions=raw.get("accident_mentions", []),
        title_concerns=raw.get("title_concerns", []),
        repair_mentions=repair_mentions,
        data_sources_available=data_sources,  # Python-set — never from LLM
        summary=raw["summary"],
    )
