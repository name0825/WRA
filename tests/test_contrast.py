import pytest

from wra.browser.contrast import CONTRAST_CANDIDATES_SCRIPT, MAX_CONTRAST_FAILURES, contrast_failures, contrast_ratio, effective_background, parse_css_color
from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult, ContrastFailure
from wra.core.models import FeatureModel
from wra.validator.visual import LOW_CONTRAST_PREFIX, find_contrast_defects, find_visual_defects


def _candidate(text: str, color: str, layers: list[str], size: float = 16, weight: int = 400) -> dict:
    return {"text": text, "color": color, "layers": layers, "size": size, "weight": weight}


def test_black_on_white_is_21_to_1() -> None:
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)


def test_parses_rgb_and_rgba_and_rejects_other_syntax() -> None:
    assert parse_css_color("rgb(10, 20, 30)") == (10, 20, 30, 1.0)
    assert parse_css_color("rgba(0, 0, 0, 0.5)") == (0, 0, 0, 0.5)
    assert parse_css_color("oklch(0.5 0.1 200)") is None


def test_translucent_background_composites_over_the_white_canvas() -> None:
    assert effective_background(["rgba(0, 0, 0, 0.5)"]) == pytest.approx((127.5, 127.5, 127.5))
    assert effective_background(["rgba(0, 0, 0, 0.5)", "rgb(0, 0, 0)"]) == pytest.approx((0, 0, 0))


def test_an_unparseable_layer_makes_the_background_unknown() -> None:
    assert effective_background(["color(srgb 1 0 0)"]) is None
    assert contrast_failures([_candidate("Title", "rgb(0, 0, 0)", ["color(srgb 0 0 0)"])]) == []


def test_dark_title_on_dark_background_fails() -> None:
    failures = contrast_failures([_candidate("Card title", "rgb(26, 42, 90)", ["rgb(11, 16, 32)"], size=20, weight=700)])

    assert len(failures) == 1
    assert failures[0].ratio < 3.0
    assert failures[0].foreground == "#1a2a5a"
    assert failures[0].background == "#0b1020"


def test_punctuation_only_text_is_decorative_and_skipped() -> None:
    assert contrast_failures([_candidate("“", "rgb(209, 212, 235)", ["rgb(251, 250, 247)"], size=96)]) == []
    assert contrast_failures([_candidate("— · —", "rgb(209, 212, 235)", ["rgb(251, 250, 247)"])]) == []


def test_large_text_uses_the_lower_threshold() -> None:
    grey_on_white = ("rgb(119, 119, 119)", ["rgb(255, 255, 255)"])

    assert len(contrast_failures([_candidate("Body", *grey_on_white, size=16)])) == 1
    assert contrast_failures([_candidate("Heading", *grey_on_white, size=24)]) == []
    assert contrast_failures([_candidate("Bold", *grey_on_white, size=19, weight=700)]) == []


def test_translucent_text_is_composited_before_measuring() -> None:
    assert len(contrast_failures([_candidate("Faint", "rgba(255, 255, 255, 0.1)", ["rgb(0, 0, 0)"])])) == 1


def test_failures_are_deduped_capped_and_sorted_worst_first() -> None:
    candidates = [_candidate(f"Text {index}", f"rgb({100 + index}, {100 + index}, {100 + index})", ["rgb(120, 120, 120)"]) for index in range(30)]
    candidates.append(dict(candidates[0]))

    failures = contrast_failures(candidates)

    assert len(failures) == MAX_CONTRAST_FAILURES
    assert [failure.ratio for failure in failures] == sorted(failure.ratio for failure in failures)


def _result(*failures: ContrastFailure, viewports: tuple[str, ...] = ("desktop",)) -> BrowserEvaluationResult:
    return BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name=name, width=1440, height=900, contrast_failures=list(failures)) for name in viewports])


def _failure(text: str, ratio: float) -> ContrastFailure:
    return ContrastFailure(text=text, ratio=ratio, foreground="#777777", background="#ffffff")


def test_a_single_marginal_failure_is_not_reported() -> None:
    assert find_contrast_defects(_result(_failure("Caption", 4.2))) == []


def test_a_single_severe_failure_is_reported() -> None:
    findings = find_contrast_defects(_result(_failure("Card title", 1.4)))

    assert len(findings) == 1
    assert findings[0].startswith(LOW_CONTRAST_PREFIX)
    assert "Card title" in findings[0]


def test_three_marginal_failures_are_not_reported() -> None:
    assert find_contrast_defects(_result(_failure("A", 4.2), _failure("B", 4.0), _failure("C", 3.8))) == []


def test_the_same_failure_at_several_viewports_counts_once() -> None:
    assert find_contrast_defects(_result(_failure("A", 4.2), _failure("B", 4.0), viewports=("desktop", "tablet", "mobile"))) == []


def test_no_contrast_findings_without_browser_evidence() -> None:
    assert find_contrast_defects(None) == []
    assert find_contrast_defects(BrowserEvaluationResult(enabled=False)) == []


def test_visual_defects_include_the_contrast_finding() -> None:
    findings = find_visual_defects("<html><head><style>body{color:#111}</style></head></html>", FeatureModel(), _result(_failure("Card title", 1.4)))

    assert [finding for finding in findings if finding.startswith(LOW_CONTRAST_PREFIX)]


def _rendered_failures(style: str, body: str) -> list[ContrastFailure]:
    return contrast_failures(_rendered_candidates(style, body))


def _rendered_candidates(style: str, body: str) -> list[dict]:
    sync_api = pytest.importorskip("playwright.sync_api")
    html = f"<!DOCTYPE html><html><head><style>body {{ margin: 0; font-size: 16px; }} {style}</style></head><body>{body}</body></html>"
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.set_content(html)
            return page.evaluate(CONTRAST_CANDIDATES_SCRIPT)
        finally:
            browser.close()


def test_oklch_text_on_oklch_background_is_measured() -> None:
    failures = _rendered_failures(
        "body { background: oklch(0.379 0.146 265.522); } p { color: oklch(0.373 0.034 259.733); }",
        "<main><p>About the company</p></main>",
    )

    assert len(failures) == 1
    assert failures[0].ratio == pytest.approx(contrast_ratio((54, 65, 83), (28, 57, 142)), abs=0.15)


def test_readable_oklch_pair_reports_no_failure() -> None:
    candidates = _rendered_candidates("body { background: oklch(0.379 0.146 265.522); } p { color: oklch(1 0 0); }", "<main><p>About the company</p></main>")

    assert len(candidates) == 1
    assert parse_css_color(candidates[0]["color"]) is not None
    assert effective_background(candidates[0]["layers"]) is not None
    assert contrast_failures(candidates) == []


@pytest.mark.parametrize("translucent", ["oklch(0.379 0.146 265.522 / 0.5)", "color-mix(in oklab, oklch(0.379 0.146 265.522) 50%, transparent)"])
def test_translucent_non_rgb_background_composites_over_its_parent(translucent: str) -> None:
    style = f"body {{ background: oklch(1 0 0); }} .card {{ background: {translucent}; padding: 12px; }} .fail {{ color: oklch(1 0 0); }} .pass {{ color: oklch(0.208 0.042 265.755); }}"
    body = '<main><div class="card"><p class="fail">Failing pair</p></div><div class="card"><p class="pass">Passing pair</p></div></main>'

    failures = _rendered_failures(style, body)

    assert [failure.text for failure in failures] == ["Failing pair"]
    assert failures[0].ratio == pytest.approx(contrast_ratio((255, 255, 255), (141.5, 156.0, 198.5)), abs=0.3)
