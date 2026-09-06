"""How content_preservation reaches the overall score at each freedom level."""

import pytest

from wra.core.models import CONTENT_GATE_THRESHOLD, EvaluationResult, content_preservation_is_a_gate
from wra.evaluator.llm import LLMEvaluator

DESIGN_DIMENSIONS = (
    "design_completeness", "visual_hierarchy", "design_consistency",
    "ux", "readability", "responsive_design", "originality", "html_quality",
)


def _result(freedom: str, content_preservation: float, design: float = 9.0) -> EvaluationResult:
    payload = {name: {"score": design, "feedback": ""} for name in DESIGN_DIMENSIONS}
    payload["content_preservation"] = {"score": content_preservation, "feedback": ""}
    payload["overall_score"] = 0.0
    payload["content_freedom"] = freedom
    return EvaluationResult.model_validate(payload)


@pytest.mark.parametrize("freedom", ["low", "medium", "high"])
def test_lower_freedom_still_averages_all_nine(freedom: str) -> None:
    assert not content_preservation_is_a_gate(freedom)
    assert _result(freedom, 10.0).overall_score == pytest.approx((9.0 * 8 + 10.0) / 9)
    assert _result(freedom, 4.0).overall_score == pytest.approx((9.0 * 8 + 4.0) / 9)


@pytest.mark.parametrize("freedom", ["ultra", "madmax"])
def test_a_compliant_page_is_scored_on_its_design_alone(freedom: str) -> None:
    """No free top mark for content the page was told it could rewrite."""
    assert content_preservation_is_a_gate(freedom)
    assert _result(freedom, 10.0).overall_score == pytest.approx(9.0)
    # The old rule handed it 9.11 for doing nothing.
    assert _result(freedom, 10.0).overall_score < (9.0 * 8 + 10.0) / 9


@pytest.mark.parametrize("freedom", ["ultra", "madmax"])
def test_a_factual_defect_caps_the_whole_score(freedom: str) -> None:
    """Strong visuals must not carry a page that misstates the source."""
    assert _result(freedom, 4.0).overall_score == pytest.approx(4.0)


@pytest.mark.parametrize("freedom", ["ultra", "madmax"])
def test_the_cap_only_bites_below_the_threshold(freedom: str) -> None:
    assert _result(freedom, CONTENT_GATE_THRESHOLD).overall_score == pytest.approx(9.0)
    assert _result(freedom, CONTENT_GATE_THRESHOLD - 0.5).overall_score == pytest.approx(CONTENT_GATE_THRESHOLD - 0.5)


def test_the_cap_never_raises_a_weak_page(tmp_path=None) -> None:
    """A passing content score cannot lift a badly designed page."""
    assert _result("madmax", 10.0, design=5.0).overall_score == pytest.approx(5.0)


def test_the_default_level_keeps_the_old_behaviour() -> None:
    """An evaluation parsed without a level is scored the way it always was."""
    payload = {name: {"score": 9.0, "feedback": ""} for name in DESIGN_DIMENSIONS}
    payload["content_preservation"] = {"score": 4.0, "feedback": ""}
    payload["overall_score"] = 0.0
    result = EvaluationResult.model_validate(payload)
    assert result.content_freedom == "low"
    assert result.overall_score == pytest.approx((9.0 * 8 + 4.0) / 9)


def test_the_evaluator_tells_a_gated_run_that_the_score_is_a_ceiling() -> None:
    gated = LLMEvaluator(llm_client=None, model="x", content_freedom="madmax")._content_freedom_instruction()
    ungated = LLMEvaluator(llm_client=None, model="x", content_freedom="high")._content_freedom_instruction()
    assert "caps it" in gated
    assert "caps it" not in ungated
