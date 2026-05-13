"""Synthesizer node — assembles the FinalReport from all agent outputs.

Responsibilities split deliberately:
  Python:  all numeric/structural fields (scores, recommendation, calc_* params,
           repair item deduplication). Deterministic and auditable.
  LLM:     key_reasons (3–5 bullet points) and summary (one paragraph) only.
           The LLM explains the recommendation; it does not produce it.

Entry point:
    synthesize(listing, vision_result, history_result, finance_result,
               finance_precomputed, errors) -> FinalReport
"""

from __future__ import annotations

import logging

import anthropic

from app.core.config import settings
from app.models.schemas import (
    ConfidenceLevel,
    EstimateSource,
    FinanceAgentResult,
    FinalReport,
    HistoryAgentResult,
    ListingInput,
    Recommendation,
    RepairEstimate,
    VisionAgentResult,
    score_to_recommendation,
)
from app.agents.finance import FinancePrecomputed

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repair item deduplication
# ---------------------------------------------------------------------------

_CONFIDENCE_RANK: dict[str, int] = {
    ConfidenceLevel.not_confident.value: 0,
    ConfidenceLevel.confident.value: 1,
    ConfidenceLevel.very_confident.value: 2,
}


def _merge_repair_items(
    vision_items: list[RepairEstimate],
    history_items: list[RepairEstimate],
) -> list[RepairEstimate]:
    """Deduplicate repair items from Vision and History by normalized component name.

    Exact lowercase match merges contributing_sources, takes the higher confidence,
    and keeps the higher cost range from either source. Non-matching items kept as-is.
    Fuzzy matching is intentionally omitted — "front bumper" and "rear bumper" would
    score high similarity but are distinct items. Post-MVP upgrade path: rapidfuzz pass
    with human review to catch near-duplicates missed by exact matching.
    """
    index: dict[str, RepairEstimate] = {}
    for item in vision_items:
        index[item.component.lower().strip()] = item
    for item in history_items:
        key = item.component.lower().strip()
        if key in index:
            existing = index[key]
            higher_confidence = (
                existing.confidence
                if _CONFIDENCE_RANK[existing.confidence.value]
                   >= _CONFIDENCE_RANK[item.confidence.value]
                else item.confidence
            )
            merged_sources: list[EstimateSource] = list(
                {*existing.contributing_sources, *item.contributing_sources}
            )
            index[key] = existing.model_copy(update={
                "contributing_sources": merged_sources,
                "confidence": higher_confidence,
                "estimated_cost_low": max(
                    existing.estimated_cost_low, item.estimated_cost_low
                ),
                "estimated_cost_high": max(
                    existing.estimated_cost_high, item.estimated_cost_high
                ),
            })
        else:
            index[key] = item
    return list(index.values())


# ---------------------------------------------------------------------------
# LLM narrative — key_reasons + summary
# ---------------------------------------------------------------------------

_SYNTHESIZER_TOOL: dict = {
    "name": "final_report_narrative",
    "description": (
        "Produce the key_reasons and summary for a vehicle purchase recommendation report. "
        "The scores, recommendation tier, and repair costs are already computed — "
        "your job is to translate them into clear, buyer-facing language."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key_reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "3–5 bullet points explaining the recommendation. "
                    "Each point should be a complete sentence naming a specific signal: "
                    "a score, a repair item, a price discrepancy, a red flag. "
                    "If any agent failed, include a bullet noting what data is missing "
                    "and how that affects confidence. Be specific — 'front bumper scratch "
                    "adds ~$800 in repair cost' is better than 'there is some cosmetic damage'."
                ),
                "minItems": 3,
                "maxItems": 5,
            },
            "summary": {
                "type": "string",
                "description": (
                    "One paragraph (3–5 sentences) plain-language recommendation. "
                    "Lead with the recommendation tier and the single most important reason. "
                    "Follow with the key trade-offs. Close with the clearest action for the buyer. "
                    "Do not repeat all bullet points verbatim — synthesise them."
                ),
            },
        },
        "required": ["key_reasons", "summary"],
    },
}


async def _generate_narrative(
    listing: ListingInput,
    vision_result: VisionAgentResult | None,
    history_result: HistoryAgentResult | None,
    finance_result: FinanceAgentResult | None,
    overall_score: float,
    recommendation: Recommendation,
    all_repair_items: list[RepairEstimate],
    errors: dict[str, str],
) -> tuple[list[str], str]:
    """LLM call to produce key_reasons and summary.

    Assembles a structured data packet and asks Sonnet to write the narrative.
    The recommendation and score are Python-computed; the LLM only writes prose.
    Returns (key_reasons, summary).
    """
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    failed_agents = list(errors.keys())
    repair_lines = "\n".join(
        f"  - {item.component} ({item.repair_category.value}): "
        f"${item.estimated_cost_low:,}–${item.estimated_cost_high:,} "
        f"[{item.confidence.value}]"
        for item in all_repair_items
    ) or "  None detected"

    data_packet = (
        f"VEHICLE: {listing.year} {listing.make} {listing.model}"
        + (f" {listing.trim}" if listing.trim else "")
        + f"\n"
        f"ASKING PRICE: ${listing.asking_price:,}  |  MILEAGE: {listing.mileage:,} miles\n\n"
        f"RECOMMENDATION: {recommendation.value}  |  OVERALL SCORE: {overall_score}/10\n\n"
        f"AGENT SCORES:\n"
        f"  Vision (condition):  {vision_result.condition_score if vision_result else 'N/A (failed)'}/10\n"
        f"  History (risk):      {history_result.risk_score if history_result else 'N/A (failed)'}/10\n"
        f"  Finance (value):     {finance_result.finance_score if finance_result and finance_result.finance_score else 'N/A (failed)'}/10\n\n"
    )

    if finance_result and finance_result.finance_score is not None:
        data_packet += (
            f"FINANCE:\n"
            f"  Estimated market value: ${finance_result.estimated_market_value:,}\n"
            f"  Price delta: ${finance_result.price_delta:+,} "
            f"({'above' if finance_result.price_delta > 0 else 'below'} market)\n"
            f"  Total repair cost: ${finance_result.total_repair_cost_low:,}–"
            f"${finance_result.total_repair_cost_high:,}\n"
            f"  All-in cost (high): "
            f"${finance_result.estimated_market_value + finance_result.total_repair_cost_high:,}\n\n"
        )

    if history_result:
        if history_result.red_flags:
            data_packet += "RED FLAGS:\n" + "\n".join(
                f"  - {f}" for f in history_result.red_flags
            ) + "\n\n"
        if history_result.accident_mentions:
            data_packet += "ACCIDENT MENTIONS:\n" + "\n".join(
                f"  - {a}" for a in history_result.accident_mentions
            ) + "\n\n"
        if history_result.title_concerns:
            data_packet += "TITLE CONCERNS:\n" + "\n".join(
                f"  - {t}" for t in history_result.title_concerns
            ) + "\n\n"

    if vision_result and vision_result.text_contradictions:
        data_packet += "LISTING CONTRADICTIONS:\n" + "\n".join(
            f"  - {c}" for c in vision_result.text_contradictions
        ) + "\n\n"

    data_packet += f"REPAIR ITEMS:\n{repair_lines}\n"

    if failed_agents:
        data_packet += (
            f"\nFAILED AGENTS: {', '.join(failed_agents)}\n"
            "Note: scores from failed agents are excluded from the overall score. "
            "Confidence is reduced accordingly.\n"
        )

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=(
            "You are a vehicle purchase analyst writing a report for a private buyer. "
            "Be direct, specific, and buyer-focused. Avoid hedging language like 'it depends' "
            "or 'you should consult a mechanic' unless genuinely warranted. "
            "Dollar amounts and specific items are more useful than generalizations."
        ),
        tools=[_SYNTHESIZER_TOOL],  # type: ignore[list-item]
        tool_choice={"type": "tool", "name": "final_report_narrative"},
        messages=[{
            "role": "user",
            "content": (
                "Using the structured data below, produce the key_reasons and summary "
                "for this vehicle analysis report.\n\n" + data_packet
            ),
        }],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "final_report_narrative":
            return block.input["key_reasons"], block.input["summary"]

    raise RuntimeError("Synthesizer LLM did not return expected tool_use block")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def synthesize(
    listing: ListingInput,
    vision_result: VisionAgentResult | None,
    history_result: HistoryAgentResult | None,
    finance_result: FinanceAgentResult | None,
    finance_precomputed: FinancePrecomputed | None,
    errors: dict[str, str],
) -> FinalReport:
    """Assemble the FinalReport from all agent outputs.

    Args:
        listing:             Original enriched listing.
        vision_result:       None if Vision node failed.
        history_result:      None if History node failed.
        finance_result:      None if Finance dependent node failed.
        finance_precomputed: None if Finance independent node failed.
                             Used for calc_* fields only.
        errors:              Dict of agent_name → error message for failed nodes.

    Returns:
        FinalReport with all fields populated. Never raises — complete failure
        falls back to overall_score=5.0 (neutral) with errors noted in key_reasons.
    """
    # ------------------------------------------------------------------
    # Python: compute all numeric / structural fields
    # ------------------------------------------------------------------
    available_scores: list[float] = []
    if vision_result:
        available_scores.append(vision_result.condition_score)
    if history_result:
        available_scores.append(history_result.risk_score)
    if finance_result and finance_result.finance_score is not None:
        available_scores.append(finance_result.finance_score)

    overall_score = (
        round(sum(available_scores) / len(available_scores), 1)
        if available_scores
        else 5.0
    )
    recommendation = score_to_recommendation(overall_score)

    vision_score = vision_result.condition_score if vision_result else None
    history_score = history_result.risk_score if history_result else None
    finance_score = (
        finance_result.finance_score
        if finance_result and finance_result.finance_score is not None
        else None
    )

    vision_items = vision_result.repair_items if vision_result else []
    history_items = history_result.repair_mentions if history_result else []
    all_repair_items = _merge_repair_items(vision_items, history_items)

    # calc_* — for client-side score recalculation when user toggles repair items
    vehicle_age = finance_precomputed["vehicle_age_years"] if finance_precomputed else 0
    market_value = (
        finance_precomputed["estimated_market_value"] if finance_precomputed else None
    )
    range_band = finance_precomputed["range_band"] if finance_precomputed else None

    # ------------------------------------------------------------------
    # LLM: key_reasons and summary
    # ------------------------------------------------------------------
    try:
        key_reasons, summary = await _generate_narrative(
            listing=listing,
            vision_result=vision_result,
            history_result=history_result,
            finance_result=finance_result,
            overall_score=overall_score,
            recommendation=recommendation,
            all_repair_items=all_repair_items,
            errors=errors,
        )
    except Exception as exc:
        logger.error("Synthesizer LLM call failed: %s", exc)
        key_reasons = [
            f"Overall score: {overall_score}/10 — {recommendation.value}",
            "Narrative generation failed — see individual agent summaries for details.",
        ]
        summary = (
            f"This vehicle received an overall score of {overall_score}/10 "
            f"({recommendation.value}). Narrative summary generation failed; "
            "please review the individual agent scores and repair items directly."
        )

    return FinalReport(
        recommendation=recommendation,
        overall_score=overall_score,
        vision_score=vision_score,
        history_score=history_score,
        finance_score=finance_score,
        key_reasons=key_reasons,
        summary=summary,
        all_repair_items=all_repair_items,
        calc_asking_price=listing.asking_price,
        calc_mileage=listing.mileage,
        calc_vehicle_age_years=vehicle_age,
        calc_estimated_market_value=market_value,
        calc_range_band=range_band,
    )
