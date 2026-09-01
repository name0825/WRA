import pytest

from wra.core.models import EvaluationResult


DIMENSIONS = (
    "design_completeness",
    "visual_hierarchy",
    "design_consistency",
    "content_preservation",
    "ux",
    "readability",
    "responsive_design",
    "originality",
    "html_quality",
)


def test_evaluation_result_accepts_flat_scores():
    payload = {name: 8 for name in DIMENSIONS}
    payload["overall_score"] = 8
    payload["feedback"] = "Overall evaluation feedback."

    result = EvaluationResult.model_validate(payload)

    assert result.overall_score == 8
    assert result.summary == "Overall evaluation feedback."
    assert all(getattr(result, name).score == 8 for name in DIMENSIONS)


def test_evaluation_result_preserves_dimension_feedback():
    payload = {name: {"score": 7, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["overall_score"] = 7

    result = EvaluationResult.model_validate(payload)

    assert all(getattr(result, name).feedback == f"{name} feedback" for name in DIMENSIONS)


def test_evaluation_result_rejects_out_of_range_score():
    payload = {name: 11 for name in DIMENSIONS}
    payload["overall_score"] = 11

    with pytest.raises(ValueError):
        EvaluationResult.model_validate(payload)
