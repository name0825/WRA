import pytest

from wra.core.models import EvaluationResult
from wra.llm.response import normalize_json_response


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


def test_evaluation_result_recalculates_overall_score_from_dimensions():
    payload = {name: {"score": 9, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["design_completeness"]["score"] = 6
    payload["overall_score"] = 10

    result = EvaluationResult.model_validate(payload)

    assert result.overall_score == pytest.approx(8.6666666667)


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


def test_evaluator_normalizes_json_markdown_fence():
    response = """```json
{"overall_score": 8}
```"""

    assert normalize_json_response(response) == '{"overall_score": 8}'


def test_evaluator_normalizes_plain_json_without_changes():
    response = '{"overall_score": 8}'

    assert normalize_json_response(response) == response


def test_evaluator_normalizes_generic_markdown_fence():
    response = """```
{"overall_score": 8}
```"""

    assert normalize_json_response(response) == '{"overall_score": 8}'


def _scored(freedom, penalty=0, **scores):
    payload = {name: {"score": 8} for name in DIMENSIONS}
    for name, score in scores.items():
        payload[name] = {"score": score, "feedback": "x"}
    return EvaluationResult.model_validate({**payload, "overall_score": 0, "content_freedom": freedom, "fact_loss_penalty": penalty})


def test_fact_loss_penalty_is_subtracted_from_the_mean_at_rewrite_permitting_levels():
    for level in ("high", "ultra", "madmax"):
        assert _scored(level, 1.0).overall_score == pytest.approx(7.0)


def test_fact_loss_penalty_is_not_applied_at_low_and_medium():
    for level in ("low", "medium"):
        assert _scored(level, 1.5).overall_score == pytest.approx(8.0)


def test_fact_loss_penalty_clamps_at_zero():
    result = _scored("madmax", 1.5, design_completeness=0, visual_hierarchy=0, design_consistency=0, ux=0, readability=0, responsive_design=0, originality=0, html_quality=0)

    assert result.overall_score == 0


def test_fact_loss_penalty_comes_before_the_content_gate_and_does_not_stack_with_it():
    assert _scored("madmax", 1.0, content_preservation=5).overall_score == pytest.approx(5.0)
    assert _scored("madmax", 1.0, content_preservation=7.5).overall_score == pytest.approx(7.0)


def test_fact_loss_penalty_cap_and_weights():
    from wra.core.models import FACT_LOSS_MAX, FACT_LOSS_PER_FINDING, fact_loss_penalty_for

    assert fact_loss_penalty_for(0) == 0
    assert fact_loss_penalty_for(1) == FACT_LOSS_PER_FINDING
    assert fact_loss_penalty_for(1000) == FACT_LOSS_MAX


def test_negative_fact_loss_penalty_is_rejected():
    with pytest.raises(ValueError):
        _scored("madmax", -1)
