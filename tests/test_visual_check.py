import json
import logging

from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult, ContrastFailure, EdgeHuggingTextElement, HeaderOverlapPair, OccludedTextElement, OverflowElement, VerticalGapBand, WrappedTextElement
from wra.evaluator.visual_check import VISUAL_CHECK_SCHEMA, VISUAL_INSPECTION_PREFIX, VisualInspector
from wra.llm.client import LLMClient
from wra.validator.visual import find_vertical_gap_defects


class FakeClient(LLMClient):
    def __init__(self, response: str = "", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "model": model, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


def _inspector(tmp_path, client: FakeClient) -> VisualInspector:
    prompt_path = tmp_path / "visual.md"
    prompt_path.write_text("Inspect the screenshots.", encoding="utf-8")
    return VisualInspector(client, prompt_path=prompt_path)


def _browser(with_screenshots: bool = True) -> BrowserEvaluationResult:
    return BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png" if with_screenshots else None),
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png" if with_screenshots else None),
    ])


def test_keeps_only_high_severity_defects(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "unreadable_text", "description": "Card titles are dark blue on navy.", "severity": "high"},
        {"viewport": "mobile", "kind": "empty_gap", "description": "Slightly loose spacing.", "severity": "low"},
        {"viewport": "desktop", "kind": "made_up", "description": "Unknown kind.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser = _browser()
    browser.viewports[0].contrast_failures = [ContrastFailure(text="Card title", ratio=1.4, foreground="#1e3a8a", background="#0b1d3a")]

    findings = _inspector(tmp_path, client).inspect(browser, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [desktop] unreadable_text: Card titles are dark blue on navy."]
    call = client.calls[0]
    assert call["images"] == ["desktop.png", "mobile.png"]
    assert call["format"] == VISUAL_CHECK_SCHEMA
    assert "<html" not in call["prompt"] and "`mobile` viewport" in call["prompt"]


def test_a_client_error_fails_soft(tmp_path, caplog) -> None:
    client = FakeClient(error=TimeoutError("timed out"))

    with caplog.at_level(logging.WARNING):
        assert _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model") == []
    assert "Visual inspection failed" in caplog.text


def test_unparseable_output_fails_soft(tmp_path) -> None:
    assert _inspector(tmp_path, FakeClient("not json")).inspect(_browser(), "<html></html>", model="vision-model") == []
    assert _inspector(tmp_path, FakeClient("[1, 2]")).inspect(_browser(), "<html></html>", model="vision-model") == []


class SequenceClient(FakeClient):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = responses

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        super().generate(prompt, model, options, **kwargs)
        return self.responses[len(self.calls) - 1]


REFUSAL = "I cannot fulfill this request because no images were provided for analysis. Please provide the screenshots for the `desktop`, `tablet`, and `mobile` viewports so that I can perform the visual inspection."


def test_a_prose_refusal_is_retried_once_and_the_second_reply_is_used(tmp_path) -> None:
    reply = json.dumps({"defects": [{"viewport": "mobile", "kind": "raw_markup", "description": "Literal tags are visible.", "severity": "high"}]})
    client = SequenceClient([REFUSAL, f"```json\n{reply}\n```"])

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [mobile] raw_markup: Literal tags are visible."]
    assert len(client.calls) == 2


def test_two_prose_refusals_fail_soft_after_exactly_two_calls(tmp_path) -> None:
    client = SequenceClient([REFUSAL, REFUSAL, REFUSAL])

    assert _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model") == []
    assert len(client.calls) == 2


def test_skipped_without_screenshots_or_browser_evidence(tmp_path) -> None:
    client = FakeClient(json.dumps({"defects": []}))
    inspector = _inspector(tmp_path, client)

    assert inspector.inspect(_browser(with_screenshots=False), "<html></html>", model="vision-model") == []
    assert inspector.inspect(BrowserEvaluationResult(enabled=False), "<html></html>", model="vision-model") == []
    assert inspector.inspect(None, "<html></html>", model="vision-model") == []
    assert client.calls == []


def test_overlapping_elements_dropped_without_dom_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "overlapping_elements", "description": "Card overlaps heading.", "severity": "high"},
    ]})
    client = FakeClient(response)

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == []


def test_overlapping_elements_kept_with_occluded_text_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "overlapping_elements", "description": "Card overlaps heading.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png", occluded_text_elements=[OccludedTextElement(selector="h1", text="Heading", occluder="div.card")]),
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png"),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [desktop] overlapping_elements: Card overlaps heading."]


def test_overlapping_elements_kept_with_header_overlap_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "mobile", "kind": "overlapping_elements", "description": "Nav items overlap.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png"),
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png", header_overlap_pairs=[HeaderOverlapPair(a="a:logo", b="a:Home", ratio=0.3)]),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [mobile] overlapping_elements: Nav items overlap."]


def test_broken_layout_dropped_without_dom_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "tablet (768px)", "kind": "broken_layout", "description": "Section text is fragmented.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="tablet", width=768, height=1024, screenshot_path="tablet.png"),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == []


def test_broken_layout_kept_with_overflow_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "390px", "kind": "broken_layout", "description": "Content spills off screen.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png", overflow_elements=[OverflowElement(selector="div.card", right=500)]),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [390px] broken_layout: Content spills off screen."]


def test_broken_layout_kept_with_render_collapse_signal(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "broken_layout", "description": "Layout looks squeezed.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png", wrapped_text_elements=[WrappedTextElement(selector="p.hero", text="S q u e e z e d", line_count=5, char_count=6)]),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [desktop] broken_layout: Layout looks squeezed."]


def test_broken_layout_dropped_when_only_a_vertical_gap_is_present(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "mobile", "kind": "broken_layout", "description": "Section is missing.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png", vertical_gap_bands=[VerticalGapBand(top=200, height=600)]),
    ])

    assert find_vertical_gap_defects(browser_result) != []

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == []


def test_unmatched_claim_viewport_checks_all_viewports(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "the screenshot", "kind": "overlapping_elements", "description": "Elements overlap somewhere.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png"),
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png", occluded_text_elements=[OccludedTextElement(selector="h1", text="Heading", occluder="div.card")]),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [the screenshot] overlapping_elements: Elements overlap somewhere."]


def test_clipped_text_worded_as_occlusion_dropped_without_occlusion_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "The heading 'Automate Your Team's Busywork with FlowStack' is partially covered by the dashboard screenshot image, making it difficult to read fully.", "severity": "high"},
        {"viewport": "desktop", "kind": "clipped_text", "description": "The paragraph is partially obscured by the dashboard screenshot image.", "severity": "high"},
    ]})
    client = FakeClient(response)

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == []


def test_clipped_text_worded_as_occlusion_kept_with_occluded_text_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "The heading 'Automate Your Team's Busywork with FlowStack' is partially covered by the dashboard screenshot image, making it difficult to read fully.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png", occluded_text_elements=[OccludedTextElement(selector="h1", text="Heading", occluder="div.card")]),
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path="mobile.png"),
    ])

    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert len(findings) == 1
    assert "[desktop] clipped_text:" in findings[0]


def test_clipped_text_cut_off_at_an_edge_dropped_without_dom_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "The company logo in the header is cut off on the right side by the screen edge, obscuring half of the 'T'.", "severity": "high"},
    ]})
    client = FakeClient(response)

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == []


def test_clipped_text_cut_off_at_the_bottom_of_the_viewport_dropped(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "tablet", "kind": "clipped_text", "description": "The header title 'Acme' is partially cut off at the bottom edge of the viewport in the tablet view.", "severity": "high"},
        {"viewport": "mobile", "kind": "clipped_text", "description": "The header title 'Acme' is partially cut off at the bottom edge of the viewport in the mobile view.", "severity": "high"},
    ]})
    client = FakeClient(response)

    browser_result = _browser()
    browser_result.viewports[1].overflow_elements = [OverflowElement(selector="h1", right=410)]
    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert findings == []


def test_clipped_text_cut_off_at_the_right_edge_kept_with_overflow_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "mobile", "kind": "clipped_text", "description": "The heading is cut off at the right edge of the viewport.", "severity": "high"},
    ]})
    client = FakeClient(response)

    browser_result = _browser()
    browser_result.viewports[1].overflow_elements = [OverflowElement(selector="h1", right=410)]
    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert len(findings) == 1
    assert "clipped_text" in findings[0]


def test_clipped_text_cut_off_by_its_container_dropped_without_dom_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "The card title is cut off by its container.", "severity": "high"},
    ]})
    client = FakeClient(response)

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == []


def test_other_kinds_mentioning_the_bottom_of_the_viewport_unaffected(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "unreadable_text", "description": "Light grey text near the bottom of the viewport is unreadable.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser = _browser()
    browser.viewports[0].contrast_failures = [ContrastFailure(text="Light grey text", ratio=1.4, foreground="#cccccc", background="#ffffff")]

    findings = _inspector(tmp_path, client).inspect(browser, "<html></html>", model="vision-model")

    assert len(findings) == 1
    assert "unreadable_text" in findings[0]


def test_unreadable_text_dropped_without_contrast_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "unreadable_text", "description": "Card titles are dark blue on navy.", "severity": "high"},
    ]})
    client = FakeClient(response)

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == []


def test_unreadable_text_kept_with_contrast_failure_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "unreadable_text", "description": "Card titles are dark blue on navy.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser = _browser()
    browser.viewports[0].contrast_failures = [ContrastFailure(text="Card title", ratio=1.4, foreground="#1e3a8a", background="#0b1d3a")]

    findings = _inspector(tmp_path, client).inspect(browser, "<html></html>", model="vision-model")

    assert findings == [f"{VISUAL_INSPECTION_PREFIX} [desktop] unreadable_text: Card titles are dark blue on navy."]


def test_unreadable_text_contrast_evidence_scoped_to_claimed_viewport(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "unreadable_text", "description": "Card titles are dark blue on navy.", "severity": "high"},
    ]})
    client = FakeClient(response)
    browser = _browser()
    browser.viewports[1].contrast_failures = [ContrastFailure(text="Card title", ratio=1.4, foreground="#1e3a8a", background="#0b1d3a")]

    findings = _inspector(tmp_path, client).inspect(browser, "<html></html>", model="vision-model")

    assert findings == []


def test_clipped_text_with_bottom_only_as_a_location_kept(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "The card title near the bottom of the screen is cut off by its container.", "severity": "high"},
        {"viewport": "desktop", "kind": "clipped_text", "description": "The paragraph below the fold is cut off on the right side of its card.", "severity": "high"},
    ]})
    client = FakeClient(response)

    browser_result = _browser()
    browser_result.viewports[0].overflow_elements = [OverflowElement(selector="h2", right=1460)]
    findings = _inspector(tmp_path, client).inspect(browser_result, "<html></html>", model="vision-model")

    assert len(findings) == 2


def test_clipped_text_cut_off_by_the_fold_dropped(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "Text is cut off by the fold.", "severity": "high"},
    ]})
    client = FakeClient(response)

    findings = _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")

    assert findings == []


def test_clipped_text_with_bottom_location_and_occlusion_wording_follows_dom_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "desktop", "kind": "clipped_text", "description": "The footer link text at the bottom of the viewport is hidden behind the cookie banner.", "severity": "high"},
    ]})
    browser_result = BrowserEvaluationResult(enabled=True, viewports=[
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path="desktop.png", occluded_text_elements=[OccludedTextElement(selector="a", text="Link", occluder="div.cookie")]),
    ])

    kept = _inspector(tmp_path, FakeClient(response)).inspect(browser_result, "<html></html>", model="vision-model")
    dropped = _inspector(tmp_path, FakeClient(response)).inspect(_browser(), "<html></html>", model="vision-model")

    assert len(kept) == 1
    assert dropped == []


def test_clipped_text_kept_with_edge_hugging_text_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "mobile", "kind": "clipped_text", "description": "The 'Limited' badge text is cut off at the right edge.", "severity": "high"},
    ]})
    browser_result = _browser()
    browser_result.viewports[1].edge_hugging_text_elements = [EdgeHuggingTextElement(selector="span.badge", section="section", left=355, right=390, text="Limited")]

    findings = _inspector(tmp_path, FakeClient(response)).inspect(browser_result, "<html></html>", model="vision-model")

    assert len(findings) == 1
    assert "[mobile] clipped_text:" in findings[0]


def test_clipped_text_evidence_scoped_to_claimed_viewport(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "mobile", "kind": "clipped_text", "description": "The 'Limited' badge text is cut off at the right edge.", "severity": "high"},
    ]})
    browser_result = _browser()
    browser_result.viewports[0].overflow_elements = [OverflowElement(selector="h1", right=1460)]
    browser_result.viewports[0].edge_hugging_text_elements = [EdgeHuggingTextElement(selector="h1", section="main", left=0, right=100, text="Heading")]

    findings = _inspector(tmp_path, FakeClient(response)).inspect(browser_result, "<span>Limited</span>", model="vision-model")

    assert findings == []


def test_clipped_text_without_viewport_match_uses_available_evidence(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "the screenshot", "kind": "clipped_text", "description": "The heading is cut off at the right edge.", "severity": "high"},
    ]})
    browser_result = _browser()
    browser_result.viewports[1].overflow_elements = [OverflowElement(selector="h1", right=410)]

    findings = _inspector(tmp_path, FakeClient(response)).inspect(browser_result, "<html></html>", model="vision-model")

    assert len(findings) == 1


def test_clipped_text_not_supported_by_unrelated_dom_metrics(tmp_path) -> None:
    response = json.dumps({"defects": [
        {"viewport": "mobile (390px)", "kind": "clipped_text", "description": "The 'Limited' badge text is cut off by its container.", "severity": "high"},
    ]})
    browser_result = _browser()
    viewport = browser_result.viewports[1]
    viewport.horizontal_overflow = True
    viewport.wrapped_text_elements = [WrappedTextElement(selector="h1", text="Heading", line_count=4, char_count=7)]
    viewport.occluded_text_elements = [OccludedTextElement(selector="h1", text="Heading", occluder="div")]

    findings = _inspector(tmp_path, FakeClient(response)).inspect(browser_result, "<span>Limited</span>", model="vision-model")

    assert findings == []


def test_a_transport_error_is_not_swallowed(tmp_path) -> None:
    import pytest

    from wra.llm.ollama import LLMTransportError

    client = FakeClient(error=LLMTransportError("connection lost"))

    with pytest.raises(LLMTransportError):
        _inspector(tmp_path, client).inspect(_browser(), "<html></html>", model="vision-model")
