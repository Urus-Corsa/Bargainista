"""Vision agent — vehicle condition assessment from photographs.

Execution flow:
    1. Parse and structure images from the normalised base64 list provided
       by the ingestion layer (ingestion.prepare_listing returns these separately
       from the enriched ListingInput).
    2. Construct a multimodal message:
         - Text block: vehicle context (year/make/model/trim, location, buyer notes)
         - Image blocks: all provided photographs in Anthropic base64 format
         - Text block: listing description for Stage 2 cross-reference (if provided)
       A single LLM call is made. The system prompt instructs the model to complete
       its photo assessment before reading the listing text, mitigating the anchoring
       problem where an LLM rationalises contradictions rather than flagging them.
    3. LLM call (claude-sonnet-4-6, tool_use) enforces VisionAgentResult structure.
    4. Post-process:
         - contributing_sources forced to ["visual"] on every repair item (Decision 20)
         - total_repair_estimate_low / _high computed by Python from item cost sums

Entry point: run(listing, images) -> VisionAgentResult
"""

from __future__ import annotations

import logging

from langsmith import tracing_context

from app.core.llm import get_anthropic_client
from app.models.schemas import (
    ConfidenceLevel,
    DamageSeverity,
    EstimateSource,
    ListingInput,
    PaintComplexity,
    RepairCategory,
    RepairEstimate,
    VisionAgentResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

# contributing_sources is intentionally absent — Python always sets ["visual"]
# after deserialization regardless of what the model produces (Decision 20).
# total_repair_estimate_low/high are absent — Python sums from repair_items.
_VISION_TOOL: dict = {
    "name": "assess_vehicle_condition",
    "description": (
        "Produce a structured condition assessment of a used vehicle "
        "based on the photographs provided in Stage 1 and the listing "
        "description cross-referenced in Stage 2."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "condition_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "Overall physical condition (integer 1–10). "
                    "10 = pristine, no visible damage. "
                    "8 = light wear only (minor chips, light swirls). "
                    "6 = one or two items requiring attention. "
                    "4 = multiple repair items or evidence of prior impact. "
                    "2 = significant damage, structural concerns, or heavy rust."
                ),
            },
            "repair_items": {
                "type": "array",
                "description": (
                    "Every damage or wear item identifiable with confidence "
                    "not_confident or higher. Include ambiguous items and mark "
                    "them not_confident — do not omit to keep the list short."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "component": {
                            "type": "string",
                            "description": (
                                "Specific part name. "
                                "e.g. 'driver door', 'front bumper cover', "
                                "'rear quarter panel', 'windshield'"
                            ),
                        },
                        "damage_type": {
                            "type": "string",
                            "description": (
                                "Nature of the damage. "
                                "e.g. 'dent', 'deep scratch', 'rust bubbling', "
                                "'paint sheen mismatch', 'crack'"
                            ),
                        },
                        "repair_category": {
                            "type": "string",
                            "enum": ["cosmetic", "mechanical", "maintenance"],
                            "description": (
                                "cosmetic = paint, dents, trim, glass. "
                                "mechanical = structural or suspension components "
                                "visible in photos. "
                                "maintenance = tires, wiper blades, visible leaks."
                            ),
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["none", "minor", "moderate", "severe"],
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["not_confident", "confident", "very_confident"],
                            "description": (
                                "very_confident: clearly visible from multiple angles "
                                "or in high resolution. "
                                "confident: visible but from one angle or partially obscured. "
                                "not_confident: ambiguous — possible lighting or "
                                "compression artifact."
                            ),
                        },
                        "inference_reasoning": {
                            "type": "string",
                            "description": (
                                "Required. One sentence: what you observed in the photo "
                                "and why you assigned this cost range. Never leave empty."
                            ),
                        },
                        "estimated_cost_low": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Low-end shop repair cost in USD (labor + parts, not DIY)",
                        },
                        "estimated_cost_high": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "High-end shop repair cost in USD",
                        },
                    },
                    "required": [
                        "component", "damage_type", "repair_category",
                        "severity", "confidence", "inference_reasoning",
                        "estimated_cost_low", "estimated_cost_high",
                    ],
                },
            },
            "text_contradictions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Conflicts between your Stage 1 photo evidence and the "
                    "Stage 2 listing description. Each entry is a specific, "
                    "factual statement quoting what you observed vs. what the "
                    "seller claims. Empty if no listing text was provided or no "
                    "contradictions exist."
                ),
            },
            "paint_complexity": {
                "type": "string",
                "enum": ["standard", "metallic", "pearl_or_tricoat"],
                "description": (
                    "Paint type detected from photos. "
                    "standard = solid color or basic clear coat. "
                    "metallic = metallic flake visible in sheen. "
                    "pearl_or_tricoat = tri-coat, color-shifting, or premium finish. "
                    "Omit this field entirely if the paint type cannot be determined."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["not_confident", "confident", "very_confident"],
                "description": (
                    "Overall confidence in the complete assessment. "
                    "very_confident: multiple photos, good lighting, clear evidence. "
                    "confident: adequate photos with some angle or lighting limitations. "
                    "not_confident: very few photos, low resolution, or no photos provided."
                ),
            },
            "summary": {
                "type": "string",
                "description": (
                    "2–3 sentences. Plain-language condition summary for the synthesiser. "
                    "Lead with the most important finding."
                ),
            },
        },
        "required": [
            "condition_score", "repair_items", "text_contradictions",
            "confidence", "summary",
        ],
    },
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a vehicle condition inspector. Your job is to assess the physical
condition of a used vehicle from photographs and produce a structured
damage report.

You work in two stages within a single response. Complete Stage 1 fully
before proceeding to Stage 2.

=================================================================
STAGE 1 — PHOTO ASSESSMENT
=================================================================

Examine every photograph provided. Base your entire assessment on what you
can observe in the images — you have not yet seen any listing text.

--- Condition score (1–10, integer) ---

  10  Pristine. No visible damage, consistent panel gaps, no oxidation.
   8  Light wear. Minor rock chips, light swirls, nothing structural.
   6  Moderate wear. One or two dents or scratches requiring attention.
   4  Notable damage. Multiple repair items, possible prior impact.
   2  Significant damage. Structural concerns, heavy rust, accident evidence.

--- Damage items ---

For each damage item you can identify:

  component           — specific part name (e.g. "driver door", "front bumper cover")
  damage_type         — nature of the damage (e.g. "dent", "deep scratch", "rust bubbling")
  repair_category     — cosmetic | mechanical | maintenance
  severity            — none | minor | moderate | severe
  confidence          — clarity of the evidence in the photos:
                          very_confident: visible from multiple angles or high resolution
                          confident: visible but single angle or partially obscured
                          not_confident: ambiguous — possible lighting or compression artifact
  inference_reasoning — one sentence: what you saw and why you assigned this cost range
  estimated_cost_low / estimated_cost_high — shop cost in USD (labor + parts)

Use the vehicle context (year, make, model, trim) to refine your assessment:

  Panel attachment type: On most unibody sedans, the rear quarter panel is structural
  (welded in) — replacement requires cutting and welding ($1,500–$4,000+). Front fenders
  are typically bolt-on ($400–$900 for the panel). When the vehicle context suggests a
  specific panel type, use the appropriate cost range.

  Trim-specific components: Sport and performance trims often have unique lower skirts,
  splitters, and side sills not found on base trims. If photos show these components
  damaged, note they may be trim-exclusive (higher cost, limited availability).

  Part availability: On vehicles older than 10 years, OEM body panels may be discontinued.
  Cost estimates should shift toward aftermarket or used OEM sourcing.

Cost anchors (adjust ±30% for high/low cost-of-living markets):

  Surface paint scratch (single panel, no primer breach)   $150 – $400
  Deep scratch (through primer, single panel)              $350 – $800
  Dent, no paint damage (PDR eligible)                     $75  – $300
  Dent with paint damage (single panel)                    $400 – $900
  Dent with paint damage (body line or edge)               $600 – $1,500
  Bumper scuff or crack (repair, no replacement)           $300 – $700
  Bumper replacement (aftermarket)                         $400 – $900
  Quarter panel, bolt-on replacement                       $500 – $1,000
  Quarter panel, structural/welded (unibody)               $1,500 – $4,000
  Side mirror replacement                                  $150 – $400
  Windshield replacement                                   $200 – $600
  Rust treatment (surface only, one panel)                 $200 – $600
  Rust repair (through-metal or structural)                $800 – $3,000+

Pearl and tri-coat resprays cost 30–50% more than standard — apply this
upward adjustment when the paint appears to be a premium finish.

--- Panel gap and paint inspection ---

These are the most reliable indicators of prior accident repair:

  Uneven panel gaps: a door, fender, or hood misaligned with adjacent panels
  indicates prior impact and re-hang or component replacement.

  Paint sheen mismatch: orange peel texture difference, metallic flake angle
  inconsistency, or color depth variation between adjacent panels indicates
  one panel was repainted while the other is original.

Both findings should appear in repair_items (with respray or structural repair
cost) AND be carried forward into Stage 2 for contradiction detection if the
listing claims no prior damage.

--- Paint complexity ---

From the photos, determine the overall paint type:
  standard          — solid color, single-stage, or standard clear coat
  metallic          — metallic flake visible in the paint sheen
  pearl_or_tricoat  — tri-coat, color-shifting, or premium special-order finish

If the photos do not show the paint clearly enough to determine type,
omit the paint_complexity field entirely.

--- Tires and glass ---

If tire or wheel photos are provided:
  - Assess visible tread depth (bald, low, adequate, good)
  - Note cracked sidewalls or uneven wear → repair_category = maintenance

If glass photos are provided:
  - Note chips, cracks, or delamination → repair_category = cosmetic

=================================================================
STAGE 2 — TEXT CROSS-REFERENCE
=================================================================

Read the listing description now — only to find contradictions with your
Stage 1 photo assessment. Do not revise condition_score or repair_items
based on seller claims.

A contradiction exists when:
  - Listing claims "no accidents" or "no prior damage" but photos show
    uneven panel gaps or mismatched paint sheen
  - Listing claims "excellent condition" but photos show dents, rust,
    or heavy scratches
  - Listing describes a specific repair as completed but the defect is
    still visible in the photos

Each contradiction must be a specific, factual statement:
  Good: "Listing states no prior accidents; driver door shows panel gap
         offset of ~4mm at trailing edge, inconsistent with factory alignment."
  Bad:  "The listing may not be accurate."

Return an empty list if no listing description is provided or no
contradictions exist.

=================================================================
OUTPUT
=================================================================

Respond using the assess_vehicle_condition tool. All fields are required
except paint_complexity. Include every damage item detectable with confidence
not_confident or higher — do not omit items to shorten the list.\
"""


# ---------------------------------------------------------------------------
# Image parsing
# ---------------------------------------------------------------------------


def _parse_image(b64: str) -> tuple[str, str]:
    """Return (media_type, raw_base64_data) from a possibly data-URI-prefixed string.

    Uploaded files from the API may include a data URI prefix such as
    'data:image/png;base64,...'. Images fetched by ingestion.py are pure base64.
    """
    if b64.startswith("data:"):
        header, data = b64.split(",", 1)
        media_type = header.split(":")[1].split(";")[0]
        return media_type, data
    return "image/jpeg", b64


# ---------------------------------------------------------------------------
# Message construction
# ---------------------------------------------------------------------------


def _build_messages(listing: ListingInput, images: list[str]) -> list[dict]:
    """Construct the multimodal user message for the LLM call.

    Layout:
      1. Vehicle context text (year/make/model/trim, location, buyer notes)
      2. N image blocks — or a text note explaining no photos were provided
      3. Stage 2 listing description text for cross-reference

    The single-call two-stage design relies on instruction sequencing: the
    system prompt commits the model to Stage 1 before it reads Stage 2.
    This avoids doubling latency and cost while still providing meaningful
    anchoring protection for the common case where photos are present.
    """
    context_lines = [
        "VEHICLE CONTEXT",
        "===============",
        f"Year:     {listing.year or 'unknown'}",
        f"Make:     {listing.make or 'unknown'}",
        f"Model:    {listing.model or 'unknown'}",
        f"Trim:     {listing.trim or 'unknown'}",
        f"Location: {listing.location}",
    ]
    if listing.user_damage_notes:
        context_lines += [
            "",
            "BUYER OBSERVATIONS",
            "(Buyer's own words about known issues — not the seller's listing copy.)",
            listing.user_damage_notes,
        ]

    content: list[dict] = [{"type": "text", "text": "\n".join(context_lines)}]

    if images:
        content.append({
            "type": "text",
            "text": (
                f"\nHere are {len(images)} photo(s) of this vehicle. "
                "Complete your full Stage 1 assessment before reading the listing text below."
            ),
        })
        for b64 in images:
            media_type, data = _parse_image(b64)
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            })
    else:
        content.append({
            "type": "text",
            "text": (
                "\nNo photographs were provided. "
                "Base your Stage 1 assessment entirely on the buyer's observations above. "
                "Set overall confidence to not_confident."
            ),
        })

    if listing.listing_description:
        stage2_text = (
            "\n=================================================================\n"
            "STAGE 2 — LISTING TEXT (cross-reference only)\n"
            "=================================================================\n\n"
            + listing.listing_description
        )
    else:
        stage2_text = (
            "\n=================================================================\n"
            "STAGE 2 — LISTING TEXT\n"
            "=================================================================\n\n"
            "(No listing description provided. Return an empty text_contradictions list.)"
        )

    content.append({"type": "text", "text": stage2_text})
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


def _build_repair_estimate(item: dict) -> RepairEstimate:
    """Convert a raw LLM repair item dict to a typed RepairEstimate.

    contributing_sources is always ["visual"] — the Vision agent reports only
    what it observes in photos (Decision 20). Python sets this field regardless
    of what the model returned.
    """
    return RepairEstimate(
        component=item["component"],
        damage_type=item["damage_type"],
        repair_category=RepairCategory(item["repair_category"]),
        contributing_sources=[EstimateSource.visual],
        confidence=ConfidenceLevel(item["confidence"]),
        severity=DamageSeverity(item["severity"]),
        inference_reasoning=item["inference_reasoning"],
        estimated_cost_low=item["estimated_cost_low"],
        estimated_cost_high=item["estimated_cost_high"],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(listing: ListingInput, images: list[str]) -> VisionAgentResult:
    """Run the Vision agent.

    Args:
        listing: Enriched ListingInput from ingestion.prepare_listing().
                 listing.trim is populated if a VIN was provided.
        images:  Flat list of base64-encoded image strings from
                 ingestion.normalise_images(). May be empty if no images
                 were provided or all URL fetches failed — agent degrades
                 to a notes-only assessment with not_confident overall.

    Returns:
        VisionAgentResult with all fields populated.
        repair_items[*].contributing_sources == ["visual"] (Python-enforced).
        total_repair_estimate_low / _high are Python sums — not LLM outputs.

    Raises:
        anthropic.APIError: if the LLM call fails.
        RuntimeError: if the LLM does not return the expected tool_use block.
    """
    logger.info(
        "Vision agent: %d image(s), vehicle=%s %s %s (trim=%s)",
        len(images),
        listing.year, listing.make, listing.model,
        listing.trim or "unknown",
    )

    messages = _build_messages(listing, images)
    client = get_anthropic_client()

    # Hide inputs from LangSmith — the message payload contains base64 image bytes
    # that exceed LangSmith's 25MB per-field limit. Outputs (token counts, content) still traced.
    with tracing_context(hide_inputs=True):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3_000,
            system=_SYSTEM_PROMPT,
            tools=[_VISION_TOOL],  # type: ignore[list-item]
            tool_choice={"type": "tool", "name": "assess_vehicle_condition"},
            messages=messages,
        )

    raw: dict | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "assess_vehicle_condition":
            raw = block.input
            break

    if raw is None:
        raise RuntimeError("Vision agent LLM did not return the expected tool_use block")

    repair_items = [_build_repair_estimate(item) for item in raw.get("repair_items", [])]

    # Python computes cost totals — not delegated to the LLM (Decision 14 principle)
    total_low = sum(r.estimated_cost_low for r in repair_items)
    total_high = sum(r.estimated_cost_high for r in repair_items)

    # paint_complexity is optional — model may omit if photos are insufficient
    raw_paint = raw.get("paint_complexity")
    paint_complexity = PaintComplexity(raw_paint) if raw_paint else None

    return VisionAgentResult(
        condition_score=raw["condition_score"],
        repair_items=repair_items,
        total_repair_estimate_low=total_low,
        total_repair_estimate_high=total_high,
        text_contradictions=raw.get("text_contradictions", []),
        paint_complexity=paint_complexity,
        confidence=ConfidenceLevel(raw["confidence"]),
        summary=raw["summary"],
    )
