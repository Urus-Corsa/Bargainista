"""SQLAlchemy ORM models.

Analysis tables:
  AnalysisRun   — one row per user-submitted analysis request
  AgentResult   — one row per agent per run (discriminated by agent_name)
  FinalReport   — one row per run, written when the synthesiser completes

Depreciation config tables (dynamic — updated via admin API, never hardcoded):
  DepreciationCategory  — retention curve per vehicle category + range band
  BrandModifier         — per-brand reliability/demand multiplier
  VariantOverride       — model/engine-specific adjustments that contradict brand average
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums (mirrored from schemas.py but kept separate so the DB enum is the
# source of truth for the column type; Pydantic enum validates at the API layer)
# ---------------------------------------------------------------------------


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"


class AgentName(str, enum.Enum):
    vision = "vision"
    history = "history"
    finance = "finance"


class RecommendationEnum(str, enum.Enum):
    GREAT_BUY = "GREAT_BUY"
    STRONG_BUY = "STRONG_BUY"
    LEAN_BUY = "LEAN_BUY"
    NEGOTIATE = "NEGOTIATE"
    NEUTRAL = "NEUTRAL"
    LEAN_PASS = "LEAN_PASS"
    STRONG_PASS = "STRONG_PASS"


# ---------------------------------------------------------------------------
# AnalysisRun
# ---------------------------------------------------------------------------


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), nullable=False, default=RunStatus.pending
    )
    # Full ListingInput stored as JSONB — no schema migration needed as input fields evolve
    listing_input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Complete serialized FinalReport — written on completion, read for late-joining WS clients
    full_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    agent_results: Mapped[list[AgentResult]] = relationship(
        "AgentResult", back_populates="run", cascade="all, delete-orphan"
    )
    final_report: Mapped[FinalReport | None] = relationship(
        "FinalReport", back_populates="run", uselist=False, cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


class AgentResult(Base):
    __tablename__ = "agent_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[AgentName] = mapped_column(
        SAEnum(AgentName, name="agent_name"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), nullable=False, default=RunStatus.pending
    )
    # Typed score column — synthesiser can aggregate directly in SQL without parsing JSON
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Full structured agent output (VisionAgentResult, HistoryAgentResult, etc.)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="agent_results")


# ---------------------------------------------------------------------------
# FinalReport
# ---------------------------------------------------------------------------


class FinalReport(Base):
    __tablename__ = "final_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # One-to-one with AnalysisRun; unique enforced at DB level
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    recommendation: Mapped[RecommendationEnum] = mapped_column(
        SAEnum(RecommendationEnum, name="recommendation_enum"), nullable=False
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    vision_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # list[str] stored as JSONB — simple and avoids a separate join table
    key_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="final_report")


# ---------------------------------------------------------------------------
# Depreciation config — dynamic data managed via admin API
# ---------------------------------------------------------------------------


class DepreciationCategory(Base):
    """Retention curve for a vehicle category.

    curve_values: JSON array of 11 floats — index = vehicle age in years (0–10).
    Values represent fraction of original MSRP retained.
    e.g. [1.0, 0.79, 0.67, ...] means year-0 = 100%, year-1 = 79%, etc.

    range_band: symmetric ± fraction applied to the point estimate at output time.
    e.g. 0.08 → low = estimate × 0.92, high = estimate × 1.08.
    """

    __tablename__ = "depreciation_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    curve_values: Mapped[list] = mapped_column(JSONB, nullable=False)
    range_band: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    source_note: Mapped[str] = mapped_column(Text, nullable=False)


class BrandModifier(Base):
    """Per-brand multiplier on the category retention curve.

    modifier: added to 1.0 before multiplying the category value.
    e.g. +0.06 for Toyota means retained = category_value × 1.06.
    Negative values reduce retention (BMW -0.04 → × 0.96).

    brand_name: stored lowercase, matched case-insensitively at query time.
    segment: informational — everyday | luxury | enthusiast | all.
    """

    __tablename__ = "brand_modifiers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    modifier: Mapped[float] = mapped_column(Float, nullable=False)
    segment: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    source_note: Mapped[str] = mapped_column(Text, nullable=False)


class VariantOverride(Base):
    """Model/engine-specific adjustment on top of the brand modifier.

    Applied when make + any model_keyword + any engine_keyword all match,
    and vehicle year falls within [year_from, year_to] (both inclusive, both nullable).

    model_keywords: JSON array of strings — any match triggers the override.
    engine_keywords: JSON array of strings — any match triggers the override.
                     If empty array, engine keyword matching is skipped.

    modifier: same sign convention as BrandModifier.
    """

    __tablename__ = "variant_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    make: Mapped[str] = mapped_column(String(64), nullable=False)
    model_keywords: Mapped[list] = mapped_column(JSONB, nullable=False)
    engine_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    year_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modifier: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    source_note: Mapped[str] = mapped_column(Text, nullable=False)
