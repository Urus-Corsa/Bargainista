"""Pydantic schemas for all agent inputs and outputs.

These are the data contracts that every agent and the orchestrator are
built against. Do not change field names or types without updating the
corresponding agent prompts and synthesiser logic.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InputMethod(str, Enum):
    url = "url"
    manual = "manual"
    vin = "vin"
    # license_plate reserved — LP-to-VIN lookup requires a paid API, not wired for MVP.
    # Do not expose this option in the frontend until the integration is complete.
    license_plate = "license_plate"


class RepairCategory(str, Enum):
    cosmetic = "cosmetic"        # paint, dents, trim, glass — detectable in photos
    mechanical = "mechanical"    # brakes, suspension, engine, transmission
    maintenance = "maintenance"  # oil, tires, filters, scheduled service items


class EstimateSource(str, Enum):
    visual = "visual"                          # detected from photos by Vision agent
    listing_mention = "listing_mention"        # explicitly stated in listing or history report text
    history_inference = "history_inference"    # deduced from service history gaps (e.g. brakes not
                                               # mentioned since 60k miles ago on a car now at 120k)


class DamageSeverity(str, Enum):
    none = "none"
    minor = "minor"
    moderate = "moderate"
    severe = "severe"


class ConfidenceLevel(str, Enum):
    not_confident = "not_confident"
    confident = "confident"
    very_confident = "very_confident"


class PaintComplexity(str, Enum):
    standard = "standard"                    # solid color or basic clear coat
    metallic = "metallic"                    # standard metallic flake
    pearl_or_tricoat = "pearl_or_tricoat"    # tri-coat, color-shifting, or premium special-order


class Recommendation(str, Enum):
    GREAT_BUY = "GREAT_BUY"    # 9.0 – 10.0  exceptional deal
    STRONG_BUY = "STRONG_BUY"  # 8.0 – 8.99  strong deal, minor concerns only
    LEAN_BUY = "LEAN_BUY"      # 7.0 – 7.99  good deal with caveats
    NEGOTIATE = "NEGOTIATE"    # 6.0 – 6.99  fair vehicle, price needs work
    NEUTRAL = "NEUTRAL"        # 5.0 – 5.99  mixed signals, go with your gut
    LEAN_PASS = "LEAN_PASS"    # 4.0 – 4.99  more concerns than positives
    STRONG_PASS = "STRONG_PASS"  # < 4.0     significant red flags, walk away


def score_to_recommendation(overall_score: float) -> Recommendation:
    """Map an overall_score (1–10) to the seven-tier Recommendation enum."""
    if overall_score >= 9.0:
        return Recommendation.GREAT_BUY
    if overall_score >= 8.0:
        return Recommendation.STRONG_BUY
    if overall_score >= 7.0:
        return Recommendation.LEAN_BUY
    if overall_score >= 6.0:
        return Recommendation.NEGOTIATE
    if overall_score >= 5.0:
        return Recommendation.NEUTRAL
    if overall_score >= 4.0:
        return Recommendation.LEAN_PASS
    return Recommendation.STRONG_PASS


# ---------------------------------------------------------------------------
# Shared repair model
# ---------------------------------------------------------------------------


class RepairEstimate(BaseModel):
    """A single repair or maintenance item, with full provenance for the interactive UI.

    contributing_sources tracks every agent/signal that flagged this item.
    The synthesiser merges duplicates across agents — a tire item flagged by both
    Vision and History becomes one RepairEstimate with both sources listed.

    confidence drives the default checkbox state in the UI:
      very_confident  → pre-checked  (multiple sources converge, or clear visual evidence)
      confident       → pre-checked  (single explicit source, no contradicting signal)
      not_confident   → unchecked    (history_inference only — deduced from absence of evidence)

    inference_reasoning is shown to the user in the UI so they understand why we
    flagged the item before deciding whether to include it in their cost calculations.
    """

    component: str = Field(
        ...,
        description="What needs repair or service. "
                    "e.g. 'front bumper', 'brake pads', 'engine oil and filter'",
    )
    damage_type: str = Field(
        ...,
        description="Nature of the issue. "
                    "e.g. 'dent', 'scratch', 'worn', 'overdue service'",
    )
    repair_category: RepairCategory
    contributing_sources: list[EstimateSource] = Field(
        ...,
        description="Which agents/signals detected this item. Multiple values mean "
                    "more than one source independently flagged the same component.",
    )
    confidence: ConfidenceLevel
    severity: DamageSeverity
    inference_reasoning: str = Field(
        ...,
        description="Plain-language explanation of why this item was flagged. "
                    "e.g. 'Rear bumper scratch visible in image 2' or "
                    "'Brake service last recorded at 62k miles; current odometer 118k — "
                    "typical service interval is 50–70k miles'.",
    )
    estimated_cost_low: int = Field(..., ge=0, description="Low end of repair estimate in USD")
    estimated_cost_high: int = Field(..., ge=0, description="High end of repair estimate in USD")


# ---------------------------------------------------------------------------
# Listing Input
# ---------------------------------------------------------------------------


class ListingInput(BaseModel):
    """What the user submits when requesting an analysis."""

    input_method: InputMethod

    # Provenance
    listing_url: HttpUrl | None = Field(
        None,
        description="URL of the original listing. Stored as provenance only — never fetched.",
    )

    # Vehicle identity.
    # VIN: if provided, ingestion auto-populates year/make/model via the free NHTSA vPIC API,
    # making manual entry optional.
    # License plate: stored as provenance only. LP-to-VIN lookup requires a paid third-party
    # API and is not integrated for MVP. Do not surface license_plate as an input option
    # in the frontend until lookup is wired.
    vin: str | None = Field(None, min_length=17, max_length=17)
    license_plate: str | None = None
    license_plate_state: str | None = Field(None, min_length=2, max_length=2)

    year: int | None = Field(None, ge=1900, le=2100)
    make: str | None = Field(None, min_length=1, max_length=64)
    model: str | None = Field(None, min_length=1, max_length=64)
    trim: str | None = Field(
        None,
        max_length=64,
        description="Trim level (e.g. 'EX', 'Sport', 'Limited'). "
                    "Auto-populated from VIN decode if VIN is provided.",
    )
    mileage: int = Field(..., ge=0, description="Odometer reading in miles")
    asking_price: int = Field(..., ge=0, description="Listed price in USD")
    location: str = Field(
        ...,
        min_length=2,
        max_length=128,
        description="City/state or zip — used for labour rate context in repair estimates",
    )

    # Listing content
    listing_description: str | None = Field(
        None,
        description="Full listing description text. Fed to the History agent for red flag "
                    "extraction and to the Vision agent as a cross-reference only — Vision "
                    "bases its assessment on photos first, then flags contradictions with text.",
    )
    history_report_text: str | None = Field(
        None,
        description="Pasted Carfax/AutoCheck report text, if the user has it.",
    )

    # Images — ingestion layer normalises both to base64 before Vision agent sees them
    image_urls: list[HttpUrl] = Field(default_factory=list)
    image_base64: list[str] = Field(
        default_factory=list,
        description="Base64-encoded images from file uploads",
    )

    # User's own observations — distinct from listing copy, safe to show Vision agent
    user_damage_notes: str | None = Field(
        None,
        description="Free-text from the user describing known issues not visible in photos. "
                    "The user's own words, not the seller's listing copy.",
    )

    @model_validator(mode="after")
    def vehicle_identity_present(self) -> ListingInput:
        has_vin = bool(self.vin)
        has_manual = all([self.year, self.make, self.model])
        if not has_vin and not has_manual:
            raise ValueError(
                "Provide a VIN (year/make/model auto-populated via NHTSA) "
                "or enter year, make, and model manually."
            )
        return self

    @model_validator(mode="after")
    def at_least_one_image_or_notes(self) -> ListingInput:
        has_images = bool(self.image_urls or self.image_base64)
        has_notes = bool(self.user_damage_notes)
        if not has_images and not has_notes:
            raise ValueError(
                "Provide at least one image (URL or upload) or user_damage_notes "
                "so the Vision agent has something to assess."
            )
        return self


# ---------------------------------------------------------------------------
# Vision Agent
# ---------------------------------------------------------------------------


class VisionAgentResult(BaseModel):
    """Structured output from the Vision agent.

    Assessment flow baked into the agent prompt:
      1. Analyze photos independently — no listing text influence at this stage.
      2. Produce repair_items, condition_score, and totals from photo evidence alone.
      3. Cross-reference the listing description (if provided) to identify contradictions.
         Text is a reaffirmer, not a driver — the photo-based assessment is never revised
         downward or upward based on what the seller claims.

    All repair_items here have contributing_sources == ["visual"].
    Text-mentioned repairs live in HistoryAgentResult.repair_mentions.
    The synthesiser merges overlapping items from both agents into FinalReport.all_repair_items.
    """

    condition_score: int = Field(
        ..., ge=1, le=10, description="10 = pristine condition, 1 = severe damage"
    )
    repair_items: list[RepairEstimate] = Field(
        default_factory=list,
        description="Damage items detected from photos. "
                    "Each item's contributing_sources will be ['visual'].",
    )
    total_repair_estimate_low: int = Field(
        ..., ge=0, description="Sum of all low-end visual repair estimates in USD"
    )
    total_repair_estimate_high: int = Field(
        ..., ge=0, description="Sum of all high-end visual repair estimates in USD"
    )
    text_contradictions: list[str] = Field(
        default_factory=list,
        description="Conflicts between photo evidence and listing text. "
                    "e.g. 'Listing claims no prior accidents; photos show offset panel gaps "
                    "on driver door consistent with prior impact repair'. "
                    "Empty if listing text was not provided or no contradictions found.",
    )
    paint_complexity: PaintComplexity | None = Field(
        None,
        description="Paint type detected from photos. Used by Finance agent to adjust "
                    "respray cost estimates upward for metallic or pearl/tri-coat finishes. "
                    "None if photos are insufficient to determine paint type.",
    )
    confidence: ConfidenceLevel
    summary: str = Field(..., description="Plain-language condition summary for the synthesiser")


# ---------------------------------------------------------------------------
# History Agent
# ---------------------------------------------------------------------------


class HistoryAgentResult(BaseModel):
    """Structured output from the History agent.

    Receives listing description and history report text. In addition to red
    flag extraction, identifies repair or maintenance items the seller mentions
    (brakes, tires, oil, etc.) and estimates their cost for the Finance agent.
    """

    risk_score: int = Field(
        ..., ge=1, le=10, description="10 = clean history, 1 = high risk"
    )
    red_flags: list[str] = Field(
        default_factory=list,
        description="Specific concerns extracted from the listing or history report",
    )
    mileage_consistent: bool = Field(
        ...,
        description="Whether stated mileage is consistent with year/price/description signals",
    )
    ownership_signals: list[str] = Field(
        default_factory=list,
        description="Notes on number of owners, lease vs. private, fleet use, etc.",
    )
    accident_mentions: list[str] = Field(
        default_factory=list,
        description="Any accident, collision, or repair mentions extracted verbatim",
    )
    title_concerns: list[str] = Field(
        default_factory=list,
        description="Salvage, rebuilt, lemon law buyback, or other title issues",
    )
    repair_mentions: list[RepairEstimate] = Field(
        default_factory=list,
        description="Repair items extracted from listing text (listing_mention) or inferred "
                    "from service history gaps (history_inference). "
                    "estimate_source is set by Python post-LLM, not by the model. "
                    "Used by the Finance agent for total cost of ownership.",
    )
    data_sources_available: list[str] = Field(
        default_factory=list,
        description="MCP tools that successfully returned data during this analysis. "
                    "e.g. ['get_vehicle_recalls', 'get_safety_ratings']. "
                    "Empty entries indicate degraded mode — synthesiser weights "
                    "risk_score lower when recall data was unavailable.",
    )
    summary: str = Field(..., description="Plain-language history summary for the synthesiser")


# ---------------------------------------------------------------------------
# Finance Agent
# ---------------------------------------------------------------------------


class FinanceAgentResult(BaseModel):
    """Structured output from the Finance agent.

    Receives Vision and History results as context. Aggregates cosmetic repair
    costs (from Vision) and mechanical/maintenance costs (from History) into a
    full cost-of-ownership picture alongside depreciation and financing analysis.

    finance_score is Optional — it is None when the independent phase failed and
    market value could not be computed. The synthesiser excludes None scores from
    the overall_score average.
    """

    finance_score: int | None = Field(
        None, ge=1, le=10, description="10 = excellent value, 1 = significantly overpriced. "
                                       "None if market value computation failed."
    )
    estimated_market_value: int = Field(
        ..., ge=0, description="Agent's estimated fair market value in USD"
    )
    price_delta: int = Field(
        ...,
        description="asking_price minus estimated_market_value. "
                    "Negative means the car is priced below market.",
    )
    total_repair_cost_low: int = Field(
        ..., ge=0,
        description="Aggregated low-end repair cost in USD — cosmetic (from Vision) "
                    "+ mechanical/maintenance (from History)",
    )
    total_repair_cost_high: int = Field(
        ..., ge=0,
        description="Aggregated high-end repair cost in USD",
    )
    depreciation_summary: str = Field(
        ...,
        description="Where this vehicle sits on its depreciation curve and projected "
                    "value in 1, 3, and 5 years",
    )
    financing_vs_cash_analysis: str = Field(
        ...,
        description="Cost of financing at typical rates vs. paying cash; "
                    "total interest over loan life",
    )
    projected_annual_maintenance: int = Field(
        ..., ge=0, description="Estimated annual maintenance cost in USD"
    )
    summary: str = Field(..., description="Plain-language finance summary for the synthesiser")


# ---------------------------------------------------------------------------
# Final Report
# ---------------------------------------------------------------------------


class FinalReport(BaseModel):
    """Synthesised output — the top-level result returned to the client.

    all_repair_items is the authoritative merged list for the interactive UI.
    The synthesiser deduplicates overlapping items from Vision and History agents,
    merging contributing_sources so the user sees one entry per component with all
    evidence listed.

    The recalculation_params block contains everything the frontend needs to
    recompute finance_score, overall_score, and recommendation client-side when
    the user toggles repair items. No server round-trip needed for interaction.
    """

    recommendation: Recommendation
    overall_score: float = Field(
        ..., ge=1.0, le=10.0,
        description="Average of available agent scores. Excludes scores from failed agents.",
    )
    # Scores are Optional — None means that agent failed and its score is excluded
    # from overall_score. The frontend renders these as N/A when None.
    vision_score: int | None = Field(None, ge=1, le=10)
    history_score: int | None = Field(None, ge=1, le=10)
    finance_score: int | None = Field(None, ge=1, le=10)
    key_reasons: list[str] = Field(
        ...,
        min_length=1,
        description="Top 3–5 bullet points explaining the recommendation",
    )
    summary: str = Field(
        ..., description="One-paragraph plain-language recommendation for the user"
    )

    # Interactive repair list — synthesiser-merged, deduplicated across all agents.
    # Each item carries contributing_sources, confidence, inference_reasoning, and cost range.
    # The frontend renders these as toggleable checkboxes.
    all_repair_items: list[RepairEstimate] = Field(
        default_factory=list,
        description="Deduplicated repair items from all agents. Use this list for the "
                    "interactive UI — not the per-agent repair_items/repair_mentions.",
    )

    # Parameters embedded for client-side score recalculation.
    # When the user toggles repair items, the frontend re-runs the finance score formula
    # and updates finance_score, overall_score, and recommendation without a server call.
    calc_asking_price: int = Field(..., description="Asking price in USD")
    calc_mileage: int = Field(..., description="Odometer reading in miles")
    calc_vehicle_age_years: int = Field(..., description="Vehicle age used in Finance agent scoring")
    # None when Finance independent phase failed — client-side recalculation disabled in that case
    calc_estimated_market_value: int | None = Field(None, description="Finance agent market value estimate")
    calc_range_band: float | None = Field(None, description="Depreciation category range band")
