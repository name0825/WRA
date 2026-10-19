import logging
from pathlib import Path

import pytest

from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
from wra.core.models import FeatureModel
from wra.designer.schema import (
    ColorSystem,
    ComponentSpec,
    Design,
    DesignSpec,
    FrameworkRecommendation,
    FrameworkRecommendations,
    InteractionSpec,
    Motion,
    ResponsiveSpec,
    SectionSpec,
    Typography,
)
from wra.evaluator.llm import LLMEvaluator, _extract_shown_text, _is_pure_rewording
from wra.validator.visual import find_wide_text_width_defects
from wra.llm.client import LLMClient
from wra.llm import context as context_module
from wra.llm.ollama import LLMTransportError


PROJECT_ROOT = Path(__file__).resolve().parent.parent

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


def _evaluation_response() -> str:
    import json

    payload = {name: {"score": 8, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["overall_score"] = 8
    payload["summary"] = "Solid."
    payload["improvement_suggestions"] = []
    return json.dumps(payload)


def make_spec() -> DesignSpec:
    return DesignSpec(
        design=Design(
            concept="Test",
            visual_direction="Minimal",
            layout="Single column",
            color_system=ColorSystem(background="#fff", surface="#f5f5f5", text="#111", muted_text="#666", primary="#000", accent="#333"),
            typography=Typography(heading="sans-serif", body="sans-serif", scale="standard", weight="400"),
            framework=FrameworkRecommendations(css=FrameworkRecommendation(name="none", reason="test"), js=FrameworkRecommendation(name="none", reason="test")),
            motion=Motion(level="none"),
        ),
        sections=[SectionSpec(id="main", purpose="Main content", layout="single", components=[ComponentSpec(type="text", purpose="Content", content_source="content.text")])],
        responsive=ResponsiveSpec(strategy="responsive"),
        interaction=InteractionSpec(),
    )


class FakeLLMClient(LLMClient):
    """Raises once, then succeeds; records each call's options."""

    def __init__(self, error: Exception, response: str) -> None:
        self.error = error
        self.response = response
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "options": options or {}, **kwargs})
        if len(self.calls) == 1:
            raise self.error
        return self.response


def _evaluator_retry(tmp_path, error: Exception):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(error, _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)
    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>")
    assert result.overall_score == 8
    assert len(client.calls) == 2
    return client


def test_evaluator_grows_num_ctx_on_a_token_limit_retry(tmp_path):
    client = _evaluator_retry(tmp_path, RuntimeError("LLM generation stopped due to output token limit"))

    first_num_ctx = client.calls[0]["options"]["num_ctx"]
    second_num_ctx = client.calls[1]["options"]["num_ctx"]
    assert second_num_ctx > first_num_ctx
    assert second_num_ctx == 32768


def test_evaluator_holds_num_ctx_on_a_schema_retry(tmp_path):
    """A larger window does not repair a wrong shape, and costs VRAM to ask for."""
    client = _evaluator_retry(tmp_path, RuntimeError("schema mismatch"))

    assert client.calls[1]["options"]["num_ctx"] == client.calls[0]["options"]["num_ctx"]


def test_clamp_warns_when_the_ceiling_binds(caplog):
    caplog.set_level(logging.WARNING, logger="wra.llm.context")
    context_module.estimate_num_ctx("x" * 100000, 1024, 32768, ceiling=2048)
    assert "num_ctx clamped" in caplog.text


def test_no_clamp_warning_when_the_prompt_fits(caplog):
    caplog.set_level(logging.WARNING, logger="wra.llm.context")
    context_module.estimate_num_ctx("short prompt", 1024, 32768, ceiling=32768)
    assert "num_ctx clamped" not in caplog.text


def test_ux_findings_are_included_in_the_evaluation_payload(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", ux_findings=['Nav/link anchor "#about" points to no element with id="about"'])

    assert "#about" in client.calls[0]["prompt"]
    assert "deterministic_ux_findings" in client.calls[0]["prompt"]


def test_visual_findings_are_included_in_the_evaluation_payload(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Unstyled: the page has no applied CSS"])

    assert "Unstyled:" in client.calls[0]["prompt"]
    assert "deterministic_visual_findings" in client.calls[0]["prompt"]


def test_ux_findings_cap_ux_score_in_code(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", ux_findings=['Nav/link anchor "#about" points to no element with id="about"'])

    assert result.ux.score <= 6.0
    assert "#about" in result.ux.feedback
    assert result.content_preservation.score == 8.0


def test_unstyled_finding_caps_visual_dimensions_in_code(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Unstyled: the page has no applied CSS"])

    assert result.visual_hierarchy.score <= 3.0
    assert result.design_consistency.score <= 3.0
    assert result.originality.score <= 3.0
    assert result.readability.score <= 3.0
    assert "Unstyled:" in result.visual_hierarchy.feedback
    assert result.content_preservation.score == 8.0
    assert result.design_completeness.score == 8.0


def test_images_dropped_finding_caps_visual_hierarchy_only(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Images dropped: none of the 2 source image(s) are referenced in the generated HTML"])

    assert result.visual_hierarchy.score <= 6.0
    assert result.design_consistency.score == 8.0
    assert result.readability.score == 8.0
    assert result.content_preservation.score == 8.0


def test_images_missing_finding_caps_visual_hierarchy_by_missing_fraction_at_madmax(tmp_path):
    def run(finding):
        evaluator = _gated_evaluator(tmp_path, [], cp_score=8.0, content_freedom="madmax")
        return evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=[finding] if finding else [])

    none_missing = run(None)
    some_missing = run("Images missing: 5 of 6 provided content image(s) are not used in the generated HTML: a.png, b.png, c.png, d.png, e.png")
    all_missing = run("Images missing: 6 of 6 provided content image(s) are not used in the generated HTML: a.png")

    assert none_missing.visual_hierarchy.score == 8.0
    assert some_missing.visual_hierarchy.score == pytest.approx(10.0 - 4.0 * 5 / 6)
    assert all_missing.visual_hierarchy.score == 6.0
    assert all_missing.visual_hierarchy.score < some_missing.visual_hierarchy.score < none_missing.visual_hierarchy.score
    assert all_missing.overall_score < none_missing.overall_score
    assert all_missing.visual_hierarchy.score < none_missing.visual_hierarchy.score
    assert all_missing.design_consistency.score == 8.0
    assert "Images missing:" in all_missing.visual_hierarchy.feedback


def test_images_missing_cap_is_logged_with_the_score_change(tmp_path, caplog):
    import json

    caplog.set_level(logging.INFO, logger="wra.evaluator.llm")
    payload = json.loads(_gated_response([], cp_score=8.0))
    payload["visual_hierarchy"]["score"] = 10
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), json.dumps(payload))
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, content_freedom="madmax", growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Images missing: 1 of 6 provided content image(s) are not used in the generated HTML: a.png"])

    assert result.visual_hierarchy.score == pytest.approx(9.33, abs=0.01)
    assert "Score cap applied: dimension=visual_hierarchy" in caplog.text


def test_low_contrast_finding_caps_visual_hierarchy_and_readability(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=['Low contrast: 3 rendered text element(s) fall below the WCAG contrast minimum, e.g. "Card title" 1.40:1 (#1a2a5a on #0b1020)'])

    assert result.visual_hierarchy.score <= 6.0
    assert result.readability.score <= 6.0
    assert "Low contrast:" in result.readability.feedback
    assert result.ux.score == 8.0
    assert result.content_preservation.score == 8.0


def test_broken_image_finding_caps_visual_hierarchy_only(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=['Broken image: 3 broken image(s) detected, e.g. "assets/bread-2.png"'])

    assert result.visual_hierarchy.score <= 6.0
    assert "Broken image:" in result.visual_hierarchy.feedback
    assert result.readability.score == 8.0
    assert result.content_preservation.score == 8.0


def _evaluator_with_payload(tmp_path, payload: dict):
    import json

    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), json.dumps(payload))
    return LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)


def _payload_with(**overrides) -> dict:
    import json

    payload = json.loads(_evaluation_response())
    payload.update(overrides)
    return payload


def test_flat_number_dimension_is_coerced_and_still_capped(tmp_path):
    evaluator = _evaluator_with_payload(tmp_path, _payload_with(ux=9))

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", ux_findings=['Nav/link anchor "#about" points to no element with id="about"'])

    assert result.ux.score == 6.0
    assert "#about" in result.ux.feedback


def test_numeric_string_dimension_is_coerced_and_still_capped(tmp_path):
    evaluator = _evaluator_with_payload(tmp_path, _payload_with(ux="9"))

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", ux_findings=['Nav/link anchor "#about" points to no element with id="about"'])

    assert result.ux.score == 6.0


def test_numeric_string_score_inside_dimension_is_still_capped(tmp_path):
    evaluator = _evaluator_with_payload(tmp_path, _payload_with(ux={"score": "9", "feedback": "fine"}))

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", ux_findings=['Nav/link anchor "#about" points to no element with id="about"'])

    assert result.ux.score == 6.0
    assert result.ux.feedback.startswith("fine")


def test_non_numeric_dimension_is_left_alone_by_the_cap():
    from wra.evaluator.llm import _apply_dimension_cap

    parsed = {"ux": "great"}
    _apply_dimension_cap(parsed, "ux", 6.0, "note")

    assert parsed == {"ux": "great"}


def test_non_finite_scores_never_become_nan_through_the_cap():
    import math

    from wra.evaluator.llm import _apply_dimension_cap

    parsed = {"ux": {"score": "nan", "feedback": ""}, "readability": "inf", "originality": {"score": "inf", "feedback": ""}}
    for dimension in parsed:
        _apply_dimension_cap(parsed, dimension, 6.0, "note")

    assert parsed["readability"] == "inf"
    assert parsed["ux"]["score"] == "nan"
    assert parsed["originality"]["score"] == "inf"
    assert not any(isinstance(entry, dict) and isinstance(entry["score"], float) and math.isnan(entry["score"]) for entry in parsed.values())


def test_single_low_contrast_element_applies_a_mild_cap(tmp_path):
    evaluator = _evaluator_with_payload(tmp_path, _payload_with(visual_hierarchy={"score": 10, "feedback": ""}, readability={"score": 10, "feedback": ""}))

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=['Low contrast: 1 rendered text element(s) fall below the WCAG contrast minimum, e.g. "Card title" 1.40:1'])

    assert result.visual_hierarchy.score == 8.0
    assert result.readability.score == 8.0


def test_low_contrast_cap_scales_down_with_the_element_count(tmp_path):
    def run(count):
        evaluator = _evaluator_with_payload(tmp_path, _payload_with(readability={"score": 10, "feedback": ""}))
        return evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=[f'Low contrast: {count} rendered text element(s) fall below the WCAG contrast minimum, e.g. "Card title" 1.40:1']).readability.score

    assert run(1) > run(2) > run(3)
    assert run(3) == 6.0
    assert run(12) == 6.0


def test_single_broken_image_applies_a_mild_cap_and_three_apply_the_full_cap(tmp_path):
    def run(count):
        evaluator = _evaluator_with_payload(tmp_path, _payload_with(visual_hierarchy={"score": 10, "feedback": ""}))
        return evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=[f'Broken image: {count} broken image(s) detected, e.g. "assets/a.png"']).visual_hierarchy.score

    assert run(1) == 8.0
    assert run(3) == 6.0


def test_a_defect_finding_without_a_parseable_count_keeps_the_full_cap(tmp_path):
    evaluator = _evaluator_with_payload(tmp_path, _payload_with(visual_hierarchy={"score": 10, "feedback": ""}))

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Broken image: some images are broken"])

    assert result.visual_hierarchy.score == 6.0


def test_exposed_markup_finding_caps_visual_hierarchy_only(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=['Exposed markup: 1 text node(s) contain leaked markup/CSS, e.g. "font-size:14px\\">"'])

    assert result.visual_hierarchy.score <= 6.0
    assert "Exposed markup:" in result.visual_hierarchy.feedback
    assert result.readability.score == 8.0
    assert result.content_preservation.score == 8.0


def test_overflow_finding_caps_responsive_design_only(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Horizontal overflow: mobile 390px: page is 470px wide (+80px), caused by section.hero"])

    assert result.responsive_design.score <= 6.0
    assert "Horizontal overflow:" in result.responsive_design.feedback
    assert result.visual_hierarchy.score == 8.0
    assert result.content_preservation.score == 8.0


def test_visual_inspection_finding_caps_visual_hierarchy_only(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", visual_findings=["Visual inspection: [desktop] overlapping_elements: A card covers the section heading."])

    assert result.visual_hierarchy.score <= 6.0
    assert "Visual inspection:" in result.visual_hierarchy.feedback
    assert result.readability.score == 8.0
    assert result.ux.score == 8.0
    assert result.content_preservation.score == 8.0


def test_low_precision_visual_inspection_kinds_do_not_cap(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(
        FeatureModel(),
        make_spec(),
        "<html></html>",
        visual_findings=[
            "Visual inspection: [desktop] empty_gap: Large blank band below the hero.",
            "Visual inspection: [mobile] clipped_text: The address is cut off at the right edge.",
        ],
    )

    assert result.visual_hierarchy.score == 8.0


def test_vertical_gap_and_oversized_header_logo_findings_do_not_cap(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(
        FeatureModel(),
        make_spec(),
        "<html></html>",
        visual_findings=[
            "Vertical gap: desktop 1440px: empty vertical band(s) at least a viewport tall, e.g. 920px starting at 500px",
            "Oversized header image: desktop 1440px: header/nav image(s) exceed a bounded logo size, e.g. img.logo 800x420px",
        ],
    )

    assert result.visual_hierarchy.score == 8.0
    assert result.design_consistency.score == 8.0
    assert result.readability.score == 8.0
    assert result.responsive_design.score == 8.0


def test_screenshot_paths_are_excluded_from_the_prompt_but_sent_as_images(tmp_path):
    from PIL import Image

    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    screenshot = tmp_path / "desktop.png"
    Image.new("RGB", (1440, 900), (255, 255, 255)).save(screenshot)
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path=str(screenshot))])

    evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", browser_result=browser_result)

    assert str(screenshot) not in client.calls[0]["prompt"]
    assert client.calls[0]["images"] == [str(screenshot)]


def test_screenshot_cap_sends_downscaled_bytes_instead_of_paths(tmp_path):
    from PIL import Image

    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    screenshot = tmp_path / "desktop.png"
    Image.new("RGB", (400, 1000), (255, 255, 255)).save(screenshot)
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0, screenshot_max_pixels=100000)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path=str(screenshot))])

    evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", browser_result=browser_result)

    images = client.calls[0]["images"]
    assert len(images) == 1 and isinstance(images[0], bytes)


def test_evaluator_prompt_requires_a_measured_overflow_correction():
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(
        client,
        "evaluator-model",
        prompt_path=PROJECT_ROOT / "prompts" / "evaluator" / "llm.md",
        min_num_ctx=1000,
        max_num_ctx=32768,
        ceiling_num_ctx=32768,
        max_retries=1,
        growth_power=2.0,
    )
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="mobile", width=390, height=844, horizontal_overflow=True, scroll_width=511, viewport_width=390)],
    )

    evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", browser_result=browser_result)

    prompt = client.calls[0]["prompt"]
    assert '"horizontal_overflow":true' in prompt
    assert '"scroll_width":511' in prompt
    assert "When any viewport has `horizontal_overflow: true`" in prompt


def test_in_page_anchor_hrefs_are_dropped_from_the_prompt(tmp_path):
    from wra.core.models import Content, Link

    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)
    features = FeatureModel(
        content=Content(
            links=[
                Link(text="Our story", href="#story"),
                Link(text="Docs", href="https://example.com/x"),
            ]
        )
    )

    evaluator.evaluate(features, make_spec(), "<html></html>")

    prompt = client.calls[0]["prompt"]
    assert '"#story"' not in prompt
    assert "Our story" in prompt
    assert "https://example.com/x" in prompt


def _gated_response(content_defects: list[dict], cp_score: float = 4.0) -> str:
    import json

    payload = {name: {"score": 8, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["content_preservation"] = {"score": cp_score, "feedback": "content_preservation feedback"}
    payload["overall_score"] = cp_score
    payload["summary"] = "Solid."
    payload["improvement_suggestions"] = []
    payload["content_defects"] = content_defects
    return json.dumps(payload)


def _gated_evaluator(tmp_path, content_defects: list[dict], cp_score: float = 4.0, content_freedom: str = "madmax"):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _gated_response(content_defects, cp_score))
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, content_freedom=content_freedom, growth_power=2.0)
    return evaluator


def test_content_gate_lifted_when_cited_price_defect_matches_correct_rendered_text(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "altered", "source_text": "389,000원", "rendered_text": "389,000원"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>389,000원</p>")

    assert result.content_preservation.score == 9.0
    assert result.content_preservation.feedback == "Cited content defects were not found in the rendered text; gate lifted."
    assert result.content_defects == []
    assert result.overall_score == pytest.approx(8.0)


def test_content_gate_lifted_when_cited_missing_item_is_actually_present(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "제 3회 청소년 ICT창업가 캠프"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<h3>제 3회 청소년 ICT창업가 캠프</h3>")

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


@pytest.mark.parametrize("cp_score", [8.0, 10.0])
def test_high_content_score_filters_refuted_missing_claim_without_changing_score(tmp_path, cp_score):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "제 3회 청소년 ICT창업가 캠프"}], cp_score=cp_score)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<h3>제 3회 청소년 ICT창업가 캠프</h3>")

    assert result.content_preservation.score == cp_score
    assert result.content_preservation.feedback == "content_preservation feedback"
    assert result.content_defects == []


def test_high_content_score_filters_only_refuted_defects(tmp_path):
    defects = [
        {"kind": "missing", "source_text": "제 3회 청소년 ICT창업가 캠프"},
        {"kind": "altered", "source_text": "2027", "rendered_text": "202tr"},
    ]
    evaluator = _gated_evaluator(tmp_path, defects, cp_score=8.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>제 3회 청소년 ICT창업가 캠프 202tr</p>")

    assert result.content_preservation.score == 8.0
    assert result.content_preservation.feedback == "content_preservation feedback"
    assert len(result.content_defects) == 1
    assert result.content_defects[0].source_text == "2027"


def test_content_gate_stays_for_confirmed_alterations(tmp_path):
    defects = [
        {"kind": "altered", "source_text": "2027", "rendered_text": "202tr"},
        {"kind": "altered", "source_text": "WRATR9-real", "rendered_text": "WRATR9"},
        {"kind": "altered", "source_text": "Bramble Analytics", "rendered_text": "Bramates Analytics"},
        {"kind": "altered", "source_text": "행복로 47", "rendered_text": "행복로 402-1234-5678 7"},
    ]
    html = "<p>202tr WRATR9 Bramates Analytics 행복로 402-1234-5678 7</p>"
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 4


def test_content_gate_stays_when_mixed_refuted_and_confirmed(tmp_path):
    defects = [
        {"kind": "altered", "source_text": "389,000원", "rendered_text": "389,000원"},
        {"kind": "altered", "source_text": "2027", "rendered_text": "202tr"},
    ]
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>389,000원 202tr</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1
    assert result.content_defects[0].source_text == "2027"


def test_content_gate_stays_for_altered_defect_despite_tag_split_rendered_text(tmp_path):
    defects = [{"kind": "altered", "source_text": "행복로 4-7", "rendered_text": "서울특별시 은평구 행복로 4 02-1234-5678 7"}]
    html = "<p>서울특별시 은평구 행복로 4<u>02-1234-5678</u> 7</p>"
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_lifted_when_missing_claim_ignores_tag_split_page_text(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "389,000원"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p><strong>3</strong>89,000원</p>")

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


def test_content_gate_stays_for_short_missing_source_text(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "2027"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>2027년 행사</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_lifted_for_confusable_dash_variant_in_rendered_text(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "altered", "source_text": "2024-01-01", "rendered_text": "2024–01–01"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>2024-01-01</p>")

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


def test_content_gate_stays_when_missing_source_only_in_noscript_and_template(tmp_path):
    defects = [{"kind": "missing", "source_text": "제 3회 청소년 ICT창업가 캠프"}]
    html = "<noscript>제 3회 청소년 ICT창업가 캠프</noscript><template>제 3회 청소년 ICT창업가 캠프</template>"
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


@pytest.mark.parametrize(
    "html",
    [
        "<p hidden>제삼회 청소년 창업가 캠프</p>",
        '<p style="display:none">제삼회 청소년 창업가 캠프</p>',
        '<div style="visibility: hidden"><p>제삼회 청소년 창업가 캠프</p></div>',
        "<style>.gone { display: none; }</style><p class='x gone'>제삼회 청소년 창업가 캠프</p>",
        "<style>#gone, .other { display:none }</style><section id='gone'><span>제삼회 청소년 창업가 캠프</span></section>",
    ],
)
def test_content_gate_stays_when_missing_source_is_only_in_hidden_markup(tmp_path, html):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "제삼회 청소년 창업가 캠프"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


@pytest.mark.parametrize(
    "html",
    [
        "<p>제삼회 청소년 창업가 캠프</p>",
        "<style>.gone { display: none; } @media (max-width: 600px) { .shown { display: none; } }</style><p class='shown'>제삼회 청소년 창업가 캠프</p><p hidden>other</p>",
        "<p hidden>숨김</p><br><p>제삼회 청소년 창업가 캠프</p>",
    ],
)
def test_content_gate_lifted_when_missing_source_is_in_shown_markup(tmp_path, html):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "제삼회 청소년 창업가 캠프"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


@pytest.mark.parametrize(
    "html",
    [
        '<div style="backface-visibility:hidden">제삼회 청소년 창업가 캠프</div>',
        "<style>.card { backface-visibility: hidden; }</style><div class='card'>제삼회 청소년 창업가 캠프</div>",
        "<style>.nav{display:none}@media(min-width:768px){.nav{display:flex}}</style><nav class='nav'>제삼회 청소년 창업가 캠프</nav>",
        "<style>.panel{display:none}.panel.active{display:block}</style><div class='panel active'>제삼회 청소년 창업가 캠프</div>",
        "<p hidden>x<p>제삼회 청소년 창업가 캠프</p>",
        "<ul><li hidden>x<li>제삼회 청소년 창업가 캠프</ul>",
    ],
)
def test_content_gate_lifted_when_static_hiding_filter_cannot_be_sure(tmp_path, html):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "제삼회 청소년 창업가 캠프"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


@pytest.mark.parametrize(
    "html",
    [
        "<p hidden>제삼회 청소년 창업가 캠프<p>본문</p>",
        "<ul><li hidden>제삼회 청소년 창업가 캠프<li>본문</ul>",
    ],
)
def test_content_gate_stays_when_hidden_text_has_omitted_end_tag(tmp_path, html):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "missing", "source_text": "제삼회 청소년 창업가 캠프"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_shown_text_block_start_tag_closes_open_hidden_paragraph():
    shown = _extract_shown_text("<p hidden>x<div>본문</div>뒤")

    assert "본문" in shown
    assert "뒤" in shown
    assert "x" not in shown


@pytest.mark.parametrize(
    "source_text, rendered_text",
    [
        ("오랜 전통과 정성으로 빚은 우리 술", "전통을 이어 정성껏 빚어낸 우리의 술"),
        ("정성을 담아 만듭니다 매일", "매일 정성껏 만들어 드립니다"),
    ],
)
def test_content_gate_lifted_for_korean_rewording_with_attached_particles_at_madmax(tmp_path, source_text, rendered_text):
    defects = [{"kind": "altered", "source_text": source_text, "rendered_text": rendered_text}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), f"<p>{rendered_text}</p>")

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


def test_content_gate_stays_for_different_three_word_product_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "무설탕 통밀 식빵", "rendered_text": "바닐라 크림 도넛"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>바닐라 크림 도넛</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_for_different_product_sharing_one_word_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "무설탕 통밀 식빵 세트 구성", "rendered_text": "바닐라 크림 도넛 세트 포함"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>바닐라 크림 도넛 세트 포함</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_when_no_defects_cited(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>anything</p>")

    assert result.content_preservation.score == 4.0
    assert result.content_defects == []


def test_content_gate_stays_when_deterministic_content_findings_present(tmp_path):
    evaluator = _gated_evaluator(tmp_path, [{"kind": "altered", "source_text": "389,000원", "rendered_text": "389,000원"}])

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>389,000원</p>", content_findings=["corrupted source token: 'x' rendered as 'y'"])

    assert result.content_preservation.score == 4.0
    assert result.content_defects == []


def test_content_gate_lift_logs_a_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="wra.evaluator.llm")
    evaluator = _gated_evaluator(tmp_path, [{"kind": "altered", "source_text": "389,000원", "rendered_text": "389,000원"}])

    evaluator.evaluate(FeatureModel(), make_spec(), "<p>389,000원</p>")

    assert "Content gate lifted" in caplog.text


def test_content_gate_lifted_for_pure_rewording_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "책상 위에 옮겨 온 오후 시의 빛.", "rendered_text": "어두운 방 한가운데, 책상 위에 닿은 빛 하나."}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>어두운 방 한가운데, 책상 위에 닿은 빛 하나.</p>")

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


def test_content_gate_stays_for_digit_carrying_altered_defect_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "매일 새벽 5시부터 반죽을 시작", "rendered_text": "이른 아침부터 정성껏 굽습니다"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>이른 아침부터 정성껏 굽습니다</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_for_rewording_at_low_freedom(tmp_path):
    defects = [{"kind": "altered", "source_text": "책상 위에 옮겨 온 오후 시의 빛.", "rendered_text": "어두운 방 한가운데, 책상 위에 닿은 빛 하나."}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="low")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>어두운 방 한가운데, 책상 위에 닿은 빛 하나.</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_for_short_item_name_altered_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "당근 케이크", "rendered_text": "레몬 파운드케이크"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>레몬 파운드케이크</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_for_rewording_when_content_findings_present(tmp_path):
    defects = [{"kind": "altered", "source_text": "책상 위에 옮겨 온 오후 시의 빛.", "rendered_text": "어두운 방 한가운데, 책상 위에 닿은 빛 하나."}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(
        FeatureModel(), make_spec(), "<p>어두운 방 한가운데, 책상 위에 닿은 빛 하나.</p>", content_findings=["corrupted source token: 'x' rendered as 'y'"]
    )

    assert result.content_preservation.score == 4.0
    assert result.content_defects == []


def test_content_gate_stays_for_near_copy_dropped_place_name_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "서울 성수 쇼룸에서 직접 켜 보세요.", "rendered_text": "성수 쇼룸에서 직접 켜 보세요."}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>성수 쇼룸에서 직접 켜 보세요.</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_for_particle_swap_near_copy_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "모듈을 빼낸 뒤 새 모듈을 끼우면 됩니다", "rendered_text": "모듈를 빼낸 뒤 새 모듈을 끼우면 됩니다"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>모듈를 빼낸 뒤 새 모듈을 끼우면 됩니다</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_stays_for_latin_leak_in_rendered_text_at_madmax(tmp_path):
    defects = [
        {
            "kind": "altered",
            "source_text": "방부제를 넣지 않기 때문에 빵은 구입 후 당일 내에 드시는 것을 권장합니다",
            "rendered_text": "빵는 구입 후 당일 내에 드시는 to 권장합니다",
        }
    ]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>빵는 구입 후 당일 내에 드시는 to 권장합니다</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_lifted_for_dropped_ordinal_list_prefix(tmp_path):
    defects = [
        {"kind": "altered", "source_text": "1. Human-Robot Interaction (HRI)", "rendered_text": "Human-Robot Interaction (HRI)"},
        {"kind": "altered", "source_text": "2. Multimodal Sensor Fusion", "rendered_text": "Multimodal Sensor Fusion"},
    ]
    html = "<h3>Human-Robot Interaction (HRI)</h3><h3>Multimodal Sensor Fusion</h3>"
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


def test_content_gate_stays_when_ordinal_prefix_strip_still_differs(tmp_path):
    defects = [{"kind": "altered", "source_text": "1. Human-Robot Interaction", "rendered_text": "Human Robot Interface"}]
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<h3>Human Robot Interface</h3>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_evaluation_result_without_content_defects_still_parses():
    from wra.core.models import EvaluationResult

    payload = {name: {"score": 8, "feedback": "ok"} for name in DIMENSIONS}
    payload["overall_score"] = 8
    payload["summary"] = "Solid."
    payload["improvement_suggestions"] = []

    result = EvaluationResult.model_validate(payload)

    assert result.content_defects == []


def test_missing_brief_fact_findings_cap_ux_only_and_leave_the_madmax_total_alone(tmp_path):
    from wra.core.models import BusinessInfo
    from wra.validator.facts import find_missing_business_facts

    features = FeatureModel(business=BusinessInfo(phone=["053-242-7498"], business_information=["사업자등록번호: 480-88-03967"]))
    findings = find_missing_business_facts("<html><body><p>053-242-7498</p></body></html>", features)
    assert findings == ["brief fact missing from page: 사업자등록번호 480-88-03967"]
    assert find_missing_business_facts("<html><body>053-242-7498 480-88-03967</body></html>", features) == []

    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, content_freedom="madmax", growth_power=2.0)

    result = evaluator.evaluate(features, make_spec(), "<html></html>", ux_findings=findings)

    assert result.ux.score == 6.0
    assert result.content_preservation.score == 8.0
    assert result.overall_score > 7.0


class TransportFailingClient(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls += 1
        raise LLMTransportError("LLM transport failure (empty_body) for model evaluator-model after 4 attempt(s)")


def test_evaluator_does_not_retry_after_a_transport_failure(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = TransportFailingClient()
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=3, growth_power=2.0)

    with pytest.raises(LLMTransportError):
        evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>")

    assert client.calls == 1


def test_the_narrow_text_finding_reaches_the_prompt_only_when_flagged(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")

    def run(median):
        client = FakeLLMClient(RuntimeError("unused"), _evaluation_response())
        evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)
        browser_result = BrowserEvaluationResult(enabled=True, wide_text_width_median=median)
        result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", browser_result=browser_result, visual_findings=find_wide_text_width_defects(browser_result))
        return client.calls[0]["prompt"], result

    flagged_prompt, flagged_result = run(332)
    unflagged_prompt, _ = run(800)

    assert "Narrow text column:" in flagged_prompt
    assert "wide_text_width_median" not in flagged_prompt
    assert flagged_result.overall_score == 8
    assert "Narrow text column:" not in unflagged_prompt
    assert "wide_text_width_median" not in unflagged_prompt


def test_content_gate_stays_when_title_alone_holds_the_missing_text(tmp_path):
    defects = [{"kind": "missing", "source_text": "제 3회 청소년 ICT창업가 캠프"}]
    html = "<html><head><title>제 3회 청소년 ICT창업가 캠프</title></head><body><p>다른 내용</p></body></html>"
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_lifted_when_missing_text_is_in_body_and_title(tmp_path):
    defects = [{"kind": "missing", "source_text": "제 3회 청소년 ICT창업가 캠프"}]
    html = "<html><head><title>제 3회 청소년 ICT창업가 캠프</title></head><body><h1>제 3회 청소년 ICT창업가 캠프</h1></body></html>"
    evaluator = _gated_evaluator(tmp_path, defects)

    result = evaluator.evaluate(FeatureModel(), make_spec(), html)

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


def test_content_gate_stays_when_a_titled_person_name_is_changed_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "김영희 대표가 오랜 전통과 정성으로 빚은 우리 술", "rendered_text": "박철수 사장이 전통을 이어 정성껏 빚어낸 우리의 술"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>박철수 사장이 전통을 이어 정성껏 빚어낸 우리의 술</p>")

    assert result.content_preservation.score == 4.0
    assert len(result.content_defects) == 1


def test_content_gate_lifted_for_rewording_that_keeps_the_titled_person_name_at_madmax(tmp_path):
    defects = [{"kind": "altered", "source_text": "오랜 전통과 정성으로 빚은 김영희 대표의 우리 술", "rendered_text": "김영희 대표가 전통을 이어 정성껏 빚어낸 우리의 술"}]
    evaluator = _gated_evaluator(tmp_path, defects, content_freedom="madmax")

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<p>김영희 대표가 전통을 이어 정성껏 빚어낸 우리의 술</p>")

    assert result.content_preservation.score == 9.0
    assert result.content_defects == []


_SOURCE_TAIL = " 정성껏 만든 빵과 신선한 커피를 매일 아침 전해 드립니다"
_RENDERED_TAIL = " 직접 구운 빵과 향긋한 커피를 아침마다 내어 드립니다"


@pytest.mark.parametrize(
    ("source_head", "rendered_head"),
    [
        ("고객님께", "회원님께"),
        ("고객님을 위해", "여러분을 위해"),
        ("선생님들이", "교사들이"),
        ("매니저님이", "담당자가"),
        ("지역 대표 상품을", "동네 인기 상품을"),
        ("가정 이사 때", "집 옮길 때"),
        ("허위 과장 없이", "거짓 없이"),
        ("석사 박사 과정생에게", "대학원생에게"),
        ("하나님께", "신께"),
        ("김영희 대표가", "김영희 대표는"),
        ("대표: 김영희", "김영희 대표가"),
        ("한국의 대표 맛", "우리나라 인기 맛"),
        ("서울의 대표 명주", "도심 속 인기 명주"),
        ("전국의 대표", "온 나라 인기"),
        ("강남의 대표 양조장", "도심 속 인기 양조장"),
        ("이곳의 대표 메뉴", "여기서 인기 메뉴"),
        ("안전한 이사 날", "믿음직한 옮기는 날"),
        ("신속한 이사 뒤", "빠른 옮기는 뒤"),
        ("정직한 대표 상품", "솔직한 인기 상품"),
        ("최고의 교수 진", "으뜸 선생 진"),
        ("유명한 박사 과정생", "이름난 연구 과정생"),
        ("고객과 대표 모두", "손님과 책임자 모두"),
        ("오래된 회장", "해묵은 어른"),
        ("이사회 이사", "운영 위원"),
        ("전통주 대표 브랜드", "민속주 대표 브랜드"),
        ("전문점 대표 메뉴", "양조장 대표 메뉴"),
        ("최고급 대표 상품", "고품격 대표 상품"),
        ("정성껏 대표 메뉴", "정갈히 대표 메뉴"),
        ("이 대표 메뉴", "우리 대표 메뉴"),
        ("한 이사 업체", "어느 이사 업체"),
        ("전국 대표 맛집", "지역 인기 맛집"),
        ("우리 대표가", "저희 대표가"),
        ("대표 이미지", "대표 사진"),
        ("이사 서비스를", "운송 서비스를"),
        ("전문가 이사 팀", "숙련된 이사 팀"),
        ("고객 대표로서", "손님 대표로서"),
        ("김영희 대표가", "김영희 대표님이"),
        ("강의는 교수가", "교수님이 맡은 강의에서"),
        ("이사는 대표가", "대표가 맡은 이사 업무에서"),
        ("모든 문의는 대표에게", "궁금한 점은 대표에게"),
        ("한약은 원장이", "약재는 원장이"),
        ("전화는 실장이", "연락은 실장이"),
        ("고민은 원장이", "걱정은 원장이"),
        ("안전은 이사의", "무탈한 이사의"),
        ("정성은 대표의", "진심 담은 대표의"),
        ("편하게 오시면 원장이", "방문하시면 원장이"),
        ("전문가 이사가", "숙련된 이사가"),
        ("한국인 교수가", "국내 교수가"),
        ("고객사 대표님", "협력사 대표님"),
        ("정비소 소장이", "수리점 소장이"),
        ("이삿짐 이사는", "운반 이사는"),
        ("신제품 박사님", "새로운 박사님"),
        ("조합원 이사의", "회원 이사의"),
        ("노동자 대표는", "근로자 대표는"),
        ("이용자 대표가", "사용자 대표가"),
        ("고객 대표가", "손님 대표가"),
        ("전문 원장이", "유명 원장이"),
        ("대표 이미지를", "대표 서비스를"),
        ("대표 한정식", "대표 전통식"),
        ("포장 이사 전문팀이", "이사 전담팀이"),
        ("이 대표가", "한 대표가"),
        pytest.param("이삿짐 이사는", "장거리 이사", marks=pytest.mark.xfail(strict=True, reason="known false positive: surname-initial common noun before 이사 on both sides")),
    ],
)
def test_rewording_with_title_like_words_but_no_changed_person_name_stays_rewording(source_head, rendered_head):
    assert _is_pure_rewording(source_head + _SOURCE_TAIL, rendered_head + _RENDERED_TAIL)


@pytest.mark.parametrize(
    ("source_head", "rendered_head"),
    [
        ("김영희 대표가", "박철수 사장이"),
        ("김영희 원장님께서", "최민수 원장님께서"),
        ("김영희 대표님께서", "박철수 대표님께서"),
        ("대표: 김영희", "대표: 박철수"),
        ("김 대표가", "박 대표가"),
        pytest.param("김영희 대표가", "저희 가게가", marks=pytest.mark.xfail(strict=True, reason="known miss: name on the source side only")),
        pytest.param("저희 가게가", "박철수 대표가", marks=pytest.mark.xfail(strict=True, reason="known miss: name on the rendered side only")),
        pytest.param("김철 대표가", "박민 대표가", marks=pytest.mark.xfail(strict=True, reason="known miss: two-syllable given name")),
        pytest.param("대표 김영희", "대표 박철수", marks=pytest.mark.xfail(strict=True, reason="known miss: title before name without a colon")),
        pytest.param("김영은 대표가", "박철수 대표가", marks=pytest.mark.xfail(strict=True, reason="known miss: name ending in 은/는/면")),
    ],
)
def test_changed_titled_person_name_is_not_rewording(source_head, rendered_head):
    assert not _is_pure_rewording(source_head + _SOURCE_TAIL, rendered_head + _RENDERED_TAIL)
