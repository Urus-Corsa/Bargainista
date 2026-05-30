"""Unit tests for app/agents/synthesizer.py.

Tests the pure Python logic: repair item deduplication and score averaging.
The LLM narrative call (_generate_narrative) is mocked throughout.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.synthesizer import _merge_repair_items, synthesize
from app.models.schemas import (
    ConfidenceLevel,
    DamageSeverity,
    EstimateSource,
    FinanceAgentResult,
    HistoryAgentResult,
    ListingInput,
    Recommendation,
    RepairCategory,
    RepairEstimate,
    VisionAgentResult,
    score_to_recommendation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_NARRATIVE = AsyncMock(return_value=(["Reason one."], "Summary sentence."))


def _repair(
    component: str,
    cost_low: int = 100,
    cost_high: int = 500,
    confidence: ConfidenceLevel = ConfidenceLevel.confident,
    sources: list[EstimateSource] | None = None,
) -> RepairEstimate:
    return RepairEstimate(
        component=component,
        damage_type="scratch",
        repair_category=RepairCategory.cosmetic,
        contributing_sources=sources or [EstimateSource.visual],
        confidence=confidence,
        severity=DamageSeverity.minor,
        inference_reasoning="Visible in photos.",
        estimated_cost_low=cost_low,
        estimated_cost_high=cost_high,
    )


def _listing() -> ListingInput:
    return ListingInput(
        input_method="manual",
        year=2019,
        make="Toyota",
        model="Camry",
        mileage=60_000,
        asking_price=18_000,
        location="Austin, TX",
        user_damage_notes="minor dent",
    )


def _vision(score: float = 7.0) -> VisionAgentResult:
    return VisionAgentResult(
        condition_score=score,
        repair_items=[],
        total_repair_estimate_low=0,
        total_repair_estimate_high=0,
        text_contradictions=[],
        confidence=ConfidenceLevel.confident,
        summary="Looks good.",
        paint_complexity=None,
    )


def _history(score: float = 6.0) -> HistoryAgentResult:
    return HistoryAgentResult(
        risk_score=score,
        red_flags=[],
        mileage_consistent=True,
        ownership_signals=[],
        accident_mentions=[],
        title_concerns=[],
        repair_mentions=[],
        data_sources_available=[],
        summary="Clean history.",
    )


def _finance(score: float = 8.0) -> FinanceAgentResult:
    return FinanceAgentResult(
        finance_score=score,
        estimated_market_value=19_000,
        price_delta=-1_000,
        total_repair_cost_low=0,
        total_repair_cost_high=0,
        depreciation_summary="Holding value well.",
        financing_vs_cash_analysis="Cash preferred.",
        projected_annual_maintenance=650,
        summary="Good deal.",
    )


# ---------------------------------------------------------------------------
# _merge_repair_items
# ---------------------------------------------------------------------------


def test_merge_empty_lists():
    assert _merge_repair_items([], []) == []


def test_merge_no_overlap_keeps_all():
    vision = [_repair("front bumper"), _repair("hood")]
    history = [_repair("engine mounts")]
    result = _merge_repair_items(vision, history)
    components = {r.component.lower() for r in result}
    assert "front bumper" in components
    assert "hood" in components
    assert "engine mounts" in components
    assert len(result) == 3


def test_merge_exact_match_deduplicates():
    vision = [_repair("front bumper")]
    history = [_repair("front bumper")]
    result = _merge_repair_items(vision, history)
    assert len(result) == 1


def test_merge_case_insensitive_match():
    vision = [_repair("Front Bumper")]
    history = [_repair("front bumper")]
    result = _merge_repair_items(vision, history)
    assert len(result) == 1


def test_merge_sources_unioned():
    vision = [_repair("front bumper", sources=[EstimateSource.visual])]
    history = [_repair("front bumper", sources=[EstimateSource.history_inference])]
    result = _merge_repair_items(vision, history)
    assert len(result) == 1
    sources = set(result[0].contributing_sources)
    assert EstimateSource.visual in sources
    assert EstimateSource.history_inference in sources


def test_merge_higher_confidence_wins():
    vision = [_repair("front bumper", confidence=ConfidenceLevel.not_confident)]
    history = [_repair("front bumper", confidence=ConfidenceLevel.very_confident)]
    result = _merge_repair_items(vision, history)
    assert result[0].confidence == ConfidenceLevel.very_confident


def test_merge_existing_higher_confidence_kept():
    vision = [_repair("front bumper", confidence=ConfidenceLevel.very_confident)]
    history = [_repair("front bumper", confidence=ConfidenceLevel.not_confident)]
    result = _merge_repair_items(vision, history)
    assert result[0].confidence == ConfidenceLevel.very_confident


def test_merge_max_cost_taken():
    vision = [_repair("front bumper", cost_low=200, cost_high=800)]
    history = [_repair("front bumper", cost_low=300, cost_high=1_200)]
    result = _merge_repair_items(vision, history)
    assert result[0].estimated_cost_low == 300
    assert result[0].estimated_cost_high == 1_200


def test_merge_vision_higher_cost_wins():
    vision = [_repair("front bumper", cost_low=400, cost_high=1_500)]
    history = [_repair("front bumper", cost_low=100, cost_high=500)]
    result = _merge_repair_items(vision, history)
    assert result[0].estimated_cost_high == 1_500


# ---------------------------------------------------------------------------
# synthesize — Python score computation (LLM mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_all_three_agents_averages_scores():
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=_vision(score=7.0),
            history_result=_history(score=6.0),
            finance_result=_finance(score=8.0),
            finance_precomputed=None,
            errors={},
        )
    # (7 + 6 + 8) / 3 = 7.0
    assert report.overall_score == pytest.approx(7.0, abs=0.1)


@pytest.mark.asyncio
async def test_synthesize_missing_history_excludes_from_average():
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=_vision(score=7.0),
            history_result=None,
            finance_result=_finance(score=9.0),
            finance_precomputed=None,
            errors={"history": "timeout"},
        )
    # (7 + 9) / 2 = 8.0
    assert report.overall_score == pytest.approx(8.0, abs=0.1)


@pytest.mark.asyncio
async def test_synthesize_finance_score_none_excluded():
    finance = _finance(score=8.0)
    finance = finance.model_copy(update={"finance_score": None})
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=_vision(score=7.0),
            history_result=_history(score=5.0),
            finance_result=finance,
            finance_precomputed=None,
            errors={},
        )
    # (7 + 5) / 2 = 6.0
    assert report.overall_score == pytest.approx(6.0, abs=0.1)


@pytest.mark.asyncio
async def test_synthesize_all_agents_none_defaults_to_5():
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=None,
            history_result=None,
            finance_result=None,
            finance_precomputed=None,
            errors={"vision": "failed", "history": "failed", "finance": "failed"},
        )
    assert report.overall_score == 5.0


@pytest.mark.asyncio
async def test_synthesize_recommendation_matches_score():
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=_vision(score=9.0),
            history_result=_history(score=9.0),
            finance_result=_finance(score=9.0),
            finance_precomputed=None,
            errors={},
        )
    assert report.recommendation == Recommendation.GREAT_BUY
    assert report.recommendation == score_to_recommendation(report.overall_score)


@pytest.mark.asyncio
async def test_synthesize_score_stored_per_agent():
    # condition_score is int on VisionAgentResult; risk_score is int on HistoryAgentResult;
    # finance_score is float on FinanceAgentResult.
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=_vision(score=8),
            history_result=_history(score=6),
            finance_result=_finance(score=8.0),
            finance_precomputed=None,
            errors={},
        )
    assert report.vision_score == 8
    assert report.history_score == 6
    assert report.finance_score == 8.0


@pytest.mark.asyncio
async def test_synthesize_none_agents_have_none_scores():
    with patch("app.agents.synthesizer._generate_narrative", new=_MOCK_NARRATIVE):
        report = await synthesize(
            listing=_listing(),
            vision_result=None,
            history_result=_history(score=7.0),
            finance_result=None,
            finance_precomputed=None,
            errors={},
        )
    assert report.vision_score is None
    assert report.history_score == 7.0
    assert report.finance_score is None
