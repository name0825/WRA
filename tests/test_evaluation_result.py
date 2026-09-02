import pytest

from wra.core.models import EvaluationResult
from wra.evaluator.llm import LLMEvaluator


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

    assert LLMEvaluator._normalize_json_response(response) == '{"overall_score": 8}'


def test_evaluator_normalizes_plain_json_without_changes():
    response = '{"overall_score": 8}'

    assert LLMEvaluator._normalize_json_response(response) == response


def test_evaluator_normalizes_generic_markdown_fence():
    response = """```
{"overall_score": 8}
```"""

    assert LLMEvaluator._normalize_json_response(response) == '{"overall_score": 8}'
