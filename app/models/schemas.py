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
    visual = "visual"                    # detected from photos by Vision agent
    listing_mention = "listing_mention"  # mentioned in listing text or history report


class DamageSeverity(str, Enum):
    none = "none"
    minor = "minor"
    moderate = "moderate"
    severe = "severe"


class ConfidenceLevel(str, Enum):
    not_confident = "not_confident"
    confident = "confident"
    very_confident = "very_confident"


class Recommendation(str, Enum):
    BUY = "BUY"
    NEGOTIATE = "NEGOTIATE"
    PASS = "PASS"


# ---------------------------------------------------------------------------
# Shared repair model
# ---------------------------------------------------------------------------


class RepairEstimate(BaseModel):
    """A single repair or maintenance item.

    Used in two places with different sources:
      - VisionAgentResult.repair_items     → estimate_source always "visual"
      - HistoryAgentResult.repair_mentions → estimate_source always "listing_mention"

    Unified type so the Finance agent can aggregate across both without
    branching on the source.
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
    estimate_source: EstimateSource
    severity: DamageSeverity
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

    All repair_items here have estimate_source == "visual".
    Text-mentioned repairs live in HistoryAgentResult.repair_mentions.
    """

    condition_score: int = Field(
        ..., ge=1, le=10, description="10 = pristine condition, 1 = severe damage"
    )
    repair_items: list[RepairEstimate] = Field(
        default_factory=list,
        description="Cosmetic damage items detected from photos. "
                    "Each item's estimate_source will be 'visual'.",
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
        description="Mechanical or maintenance repair items mentioned in the listing text. "
                    "Each item's estimate_source will be 'listing_mention'. "
                    "Used by the Finance agent for total cost of ownership.",
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
    """

    finance_score: int = Field(
        ..., ge=1, le=10, description="10 = excellent value, 1 = significantly overpriced"
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
    """Synthesised output — the top-level result returned to the client."""

    recommendation: Recommendation
    overall_score: float = Field(
        ..., ge=1.0, le=10.0, description="Simple average of the three agent scores"
    )
    vision_score: int = Field(..., ge=1, le=10)
    history_score: int = Field(..., ge=1, le=10)
    finance_score: int = Field(..., ge=1, le=10)
    key_reasons: list[str] = Field(
        ...,
        min_length=1,
        description="Top 3–5 bullet points explaining the recommendation",
    )
    summary: str = Field(
        ..., description="One-paragraph plain-language recommendation for the user"
    )
