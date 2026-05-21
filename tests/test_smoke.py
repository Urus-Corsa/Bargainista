"""Smoke tests — pure logic, no I/O, no external services required."""

from app.models.schemas import Recommendation, score_to_recommendation


def test_score_to_recommendation_boundaries():
    assert score_to_recommendation(9.0) == Recommendation.GREAT_BUY
    assert score_to_recommendation(8.0) == Recommendation.STRONG_BUY
    assert score_to_recommendation(7.0) == Recommendation.LEAN_BUY
    assert score_to_recommendation(6.0) == Recommendation.NEGOTIATE
    assert score_to_recommendation(5.0) == Recommendation.NEUTRAL
    assert score_to_recommendation(4.0) == Recommendation.LEAN_PASS
    assert score_to_recommendation(3.9) == Recommendation.STRONG_PASS


def test_recommendation_enum_values():
    values = {r.value for r in Recommendation}
    assert "GREAT_BUY" in values
    assert "STRONG_PASS" in values
    assert len(values) == 7
