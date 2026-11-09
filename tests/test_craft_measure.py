from pathlib import Path

import pytest

from wra.browser.craft_measure import measure_craft
from wra.browser.guard import launch_guarded
from wra.browser.models import BrowserCraftMeasurements, BrowserEvaluationResult
from wra.core.models import FeatureModel
from wra.validator.visual import browser_craft_metrics, find_refinable_browser_defects, find_visual_defects

pytest.importorskip("playwright.sync_api")
import playwright.sync_api as pw

PARAGRAPH = "This paragraph carries enough visible words to count as real page content for the measurement. "
REVEAL_BLOCKS = "".join(f'<section class="reveal"><p>{PARAGRAPH * 2}</p></section>' for _ in range(4))

JS_REVEAL_PAGE = f"""<!DOCTYPE html><html><head><style>
body{{margin:0}}section{{padding:2rem}}.reveal{{opacity:0;transform:translateY(30px);transition:opacity .6s,transform .6s}}.reveal.on{{opacity:1;transform:none}}
</style></head><body>{REVEAL_BLOCKS}
<script>const io=new IntersectionObserver(es=>es.forEach(e=>{{if(e.isIntersecting)e.target.classList.add('on')}}));document.querySelectorAll('.reveal').forEach(el=>io.observe(el));</script>
</body></html>"""
VISIBLE_BY_DEFAULT_PAGE = f"""<!DOCTYPE html><html><head><style>
body{{margin:0}}section{{padding:2rem}}.js .reveal{{opacity:0;transition:opacity .6s}}.js .reveal.on{{opacity:1}}
</style></head><body>{REVEAL_BLOCKS}
<script>document.documentElement.classList.add('js');</script>
</body></html>"""
NOSCRIPT_RESCUED_PAGE = f"""<!DOCTYPE html><html><head><style>
body{{margin:0}}section{{padding:2rem}}.reveal{{opacity:0}}
</style><noscript><style>.reveal{{opacity:1!important}}</style></noscript></head><body>{REVEAL_BLOCKS}</body></html>"""
DISPLAY_NONE_PAGE = f"""<!DOCTYPE html><html><head><style>
body{{margin:0}}.panel{{display:none}}.menu{{opacity:0;visibility:hidden;position:fixed;top:0;left:0}}
</style></head><body><p>{PARAGRAPH}</p><div class="panel"><p>{PARAGRAPH * 6}</p></div><div class="menu"><p>{PARAGRAPH * 6}</p></div></body></html>"""
KEYFRAME_REVEAL_PAGE = f"""<!DOCTYPE html><html><head><style>
body{{margin:0}}section{{padding:2rem;opacity:0;animation:fadeUp .3s ease-out forwards}}@keyframes fadeUp{{from{{opacity:0}}to{{opacity:1}}}}
</style></head><body>{REVEAL_BLOCKS.replace(' class="reveal"', '')}</body></html>"""

HERO_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<nav><a href="#a" class="btn">Menu button</a></nav>
<h1>Welcome to the studio</h1><a href="#start" class="btn" style="display:inline-block;padding:12px 24px">Get started</a>
</body></html>"""
LOW_HERO_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div style="height:1200px"></div><h1>Welcome to the studio</h1><a href="#start" class="btn">Get started</a>
</body></html>"""
NAV_ONLY_CTA_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<nav><a href="#start" class="btn">Get started</a></nav><h1>Welcome to the studio</h1><p>Plain paragraph and a <a href="#x">plain link</a>.</p>
</body></html>"""
HERO_IN_HEADER_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<header style="padding-top:150px"><h1>Welcome to the studio</h1><a href="#start" class="btn">Get started</a></header>
</body></html>"""
LOGO_HEADER_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<header><h1 class="logo">Brand</h1><a href="#about">About us</a><a href="#start" class="btn">Contact us</a></header>
</body></html>"""
INLINE_LINK_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<section style="padding-top:150px"><h1>Today at the cafe</h1><p>Read the <a href="#story">full story</a> of the place.</p></section>
</body></html>"""
HEADER_WITHOUT_HEADING_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<header><a href="#start" class="btn">Get started</a></header><div><h1>Welcome to the studio</h1></div>
</body></html>"""
GRADIENT_BUTTON_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div><h1>Welcome to the studio</h1></div>
<a href="#order" style="display:inline-block;padding:10px 20px;background-image:linear-gradient(#f00,#00f)">Order now</a>
</body></html>"""
TEXT_LINK_CTA_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<nav><a href="#a">Home</a></nav>
<section style="padding-top:150px"><h1>Today at the cafe</h1><a href="#menu" class="nav-link">See today's menu</a></section>
</body></html>"""
TEXT_LINK_OTHER_SECTION_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<section><h1>Today at the cafe</h1></section><section><a href="#menu" class="nav-link">See today's menu</a></section>
</body></html>"""
ICON_TOGGLE_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div><h1>Welcome to the studio</h1><button type="button">&#9776;</button></div>
</body></html>"""
NARROW_BUTTON_PAGE ="""<!DOCTYPE html><html><body style="margin:0">
<button style="width:60px;font-size:16px">Get started now</button><button>Go</button>
</body></html>"""


def _measure(html: str, tmp_path: Path) -> BrowserCraftMeasurements | None:
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    with pw.sync_playwright() as playwright:
        browser = launch_guarded(playwright)
        try:
            return measure_craft(browser, (tmp_path / "index.html").resolve().as_uri(), tmp_path.resolve(), 10000)
        finally:
            browser.close()


def test_scroll_reveal_text_that_needs_javascript_is_counted_as_hidden(tmp_path: Path) -> None:
    craft = _measure(JS_REVEAL_PAGE, tmp_path)

    assert craft is not None
    assert craft.js_off_hidden_chars >= 0.9 * craft.js_off_text_chars
    assert craft.js_off_hidden_chars > 500
    assert "section.reveal" in craft.js_off_hidden_selectors


@pytest.mark.parametrize("page", [VISIBLE_BY_DEFAULT_PAGE, NOSCRIPT_RESCUED_PAGE, DISPLAY_NONE_PAGE])
def test_content_that_is_visible_without_javascript_is_not_counted_as_hidden(tmp_path: Path, page: str) -> None:
    craft = _measure(page, tmp_path)

    assert craft is not None
    assert craft.js_off_text_chars > 0
    assert craft.js_off_hidden_chars == 0


def test_a_css_keyframe_reveal_is_not_counted_as_hidden(tmp_path: Path) -> None:
    craft = _measure(KEYFRAME_REVEAL_PAGE, tmp_path)

    assert craft is not None
    assert craft.js_off_hidden_chars == 0


def test_a_first_screen_heading_and_button_are_detected(tmp_path: Path) -> None:
    craft = _measure(HERO_PAGE, tmp_path)

    assert craft is not None
    assert craft.first_screen_h1 is True
    assert craft.first_screen_cta is True


def test_a_heading_below_the_first_screen_is_not_detected(tmp_path: Path) -> None:
    craft = _measure(LOW_HERO_PAGE, tmp_path)

    assert craft is not None
    assert craft.first_screen_h1 is False
    assert craft.first_screen_cta is False


def test_a_button_only_in_the_nav_is_not_a_first_screen_cta(tmp_path: Path) -> None:
    craft = _measure(NAV_ONLY_CTA_PAGE, tmp_path)

    assert craft is not None
    assert craft.first_screen_h1 is True
    assert craft.first_screen_cta is False


@pytest.mark.parametrize("page", [HERO_IN_HEADER_PAGE, GRADIENT_BUTTON_PAGE, TEXT_LINK_CTA_PAGE])
def test_a_first_screen_cta_is_found_in_a_hero_header_a_gradient_button_or_a_text_link(tmp_path: Path, page: str) -> None:
    craft = _measure(page, tmp_path)

    assert craft is not None
    assert craft.first_screen_cta is True


@pytest.mark.parametrize("page", [HEADER_WITHOUT_HEADING_PAGE, TEXT_LINK_OTHER_SECTION_PAGE, ICON_TOGGLE_PAGE, LOGO_HEADER_PAGE, INLINE_LINK_PAGE])
def test_a_header_button_a_far_text_link_and_an_icon_toggle_are_not_first_screen_ctas(tmp_path: Path, page: str) -> None:
    craft = _measure(page, tmp_path)

    assert craft is not None
    assert craft.first_screen_cta is False


def test_a_button_label_wrapped_onto_two_lines_is_reported(tmp_path: Path) -> None:
    craft = _measure(NARROW_BUTTON_PAGE, tmp_path)

    assert craft is not None
    assert [label.text for label in craft.wrapped_button_labels] == ["Get started now"]
    assert craft.wrapped_button_labels[0].line_count >= 2


def _result(hidden: int, total: int, enabled: bool = True, craft: bool = True) -> BrowserEvaluationResult:
    measurements = BrowserCraftMeasurements(js_off_text_chars=total, js_off_hidden_chars=hidden, js_off_hidden_selectors=["div.reveal"]) if craft else None
    return BrowserEvaluationResult(enabled=enabled, craft=measurements)


def test_refinable_browser_defects_are_empty_without_a_usable_measurement() -> None:
    assert find_refinable_browser_defects(None) == []
    assert find_refinable_browser_defects(_result(900, 1000, enabled=False)) == []
    assert find_refinable_browser_defects(_result(900, 1000, craft=False)) == []


def test_refinable_browser_defects_ignore_small_or_minor_hidden_content() -> None:
    assert find_refinable_browser_defects(_result(150, 160)) == []
    assert find_refinable_browser_defects(_result(300, 1000)) == []


def test_refinable_browser_defects_report_mostly_hidden_content() -> None:
    findings = find_refinable_browser_defects(_result(600, 1000))

    assert len(findings) == 1
    assert "600 of 1000" in findings[0]
    assert "div.reveal" in findings[0]


def test_browser_craft_metrics_is_empty_without_a_measurement() -> None:
    assert browser_craft_metrics(_result(0, 0, craft=False)) == {}
    assert browser_craft_metrics(_result(600, 1000))["js_off_hidden_ratio"] == 0.6


def test_the_craft_measurement_does_not_reach_the_evaluator_visual_findings() -> None:
    html = "<html><head><style>body{color:#111}</style></head><body><p>text</p></body></html>"

    assert find_visual_defects(html, FeatureModel(), _result(900, 1000)) == find_visual_defects(html, FeatureModel(), _result(0, 0, craft=False))


def test_the_real_framework_catalog_localizes_a_bootstrap_page(tmp_path: Path) -> None:
    from wra.browser.evaluator import _localize_framework_cdns
    from wra.browser.framework_cache import DEFAULT_CACHE_DIR
    from wra.core.config import load_config

    frameworks = load_config().frameworks
    css_url = frameworks.css[0].cdn
    html = f'<html><head><link rel="stylesheet" href="{css_url}"></head><body></body></html>'
    try:
        localized = _localize_framework_cdns(html, frameworks, DEFAULT_CACHE_DIR, tmp_path)
    except Exception as exc:
        pytest.skip(f"framework cache unavailable offline: {exc}")

    assert css_url not in localized
    assert "cdn.jsdelivr" not in localized
