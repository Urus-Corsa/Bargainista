"""SQLAlchemy ORM models.

Three tables:
  AnalysisRun   — one row per user-submitted analysis request
  AgentResult   — one row per agent per run (discriminated by agent_name)
  FinalReport   — one row per run, written when the synthesiser completes
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
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
    BUY = "BUY"
    NEGOTIATE = "NEGOTIATE"
    PASS = "PASS"


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
    vision_score: Mapped[int] = mapped_column(Integer, nullable=False)
    history_score: Mapped[int] = mapped_column(Integer, nullable=False)
    finance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # list[str] stored as JSONB — simple and avoids a separate join table
    key_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="final_report")
