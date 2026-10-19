import base64
import re
import socket
import struct
import threading
import time
import zlib
from pathlib import Path

import pytest

from wra.browser.evaluator import FRAMEWORK_CACHE_DIRNAME, HTTP_CACHE_DIRNAME, PREVIEW_NAME, BrowserEvaluator
from wra.browser.guard import _make_route_handler, _path_from_file_url
from wra.browser.framework_cache import cached_path_for_url, iter_framework_urls
from wra.browser.models import BrowserConfig, BrowserViewport
from wra.browser.reveal import reveal_animated_content
from wra.core.models import Framework, FrameworkConfig

pytest.importorskip("playwright.sync_api")
import playwright.sync_api as pw

PAGE = '<html><body style="margin:0"><h1>Hi</h1><img src="assets/hero.png" width="400"></body></html>'
REVEAL_PAGE = """<!DOCTYPE html><html><head><style>
body{margin:0;background:#fff}section{min-height:90vh;padding:4rem}.reveal{width:320px;height:180px;opacity:0;background:#102030;transform:translateY(40px);transition:opacity .6s,transform .6s}.reveal.on{opacity:1;transform:none}
</style></head><body>
<section><div class="reveal"></div></section><section><div class="reveal"></div></section><section><div class="reveal"></div></section>
<script>const io=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting)entry.target.classList.add('on')}),{threshold:0.1});document.querySelectorAll('.reveal').forEach(el=>io.observe(el));</script>
</body></html>"""
GROWING_REVEAL_PAGE = """<!DOCTYPE html><html><head><style>
body{margin:0}.reveal{width:320px;height:180px;opacity:0;background:#102030}
</style></head><body><main style="height:1800px"><div class="reveal"></div></main>
<script>addEventListener('scroll',()=>{const item=document.createElement('div');item.className='reveal';item.style.height='600px';document.body.append(item)})</script>
</body></html>"""
TRANSLATED_REVEAL_PAGE = """<!DOCTYPE html><html><head><style>
body{margin:0}.reveal{width:320px;height:180px;opacity:0;background:#102030;transform:translateX(600px)}
</style></head><body><div class="reveal"></div></body></html>"""

FADE_UP_KEYFRAME_PAGE = """<!DOCTYPE html><html><head><style>
body{margin:0}.fade-up{width:320px;height:180px;opacity:0;background:#102030;animation:fadeUp .3s ease-out forwards}
@keyframes fadeUp{from{opacity:0}to{opacity:1}}
</style></head><body><div class="fade-up"></div></body></html>"""


def _png(path: Path, width: int, height: int) -> None:
    raw = b"".join(b"\0" + bytes((20, 40, 90)) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    _png(tmp_path / "assets" / "hero.png", 200, 120)
    return tmp_path


def _evaluate(html: str, run_dir: Path, output_dir: Path, asset_root: Path | None):
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    return BrowserEvaluator(config).evaluate(html=html, output_dir=output_dir, iteration=1, asset_root=asset_root)


LOW_CONTRAST_PAGE = """<!DOCTYPE html><html><body style="margin:0;background:#0b1020;color:#e8ecf5">
<h2 style="color:#1a2a5a;font-size:20px">Card title</h2><p>Readable body text</p>
</body></html>"""
READABLE_PAGE = """<!DOCTYPE html><html><body style="margin:0;background:#fff;color:#111">
<h1>Readable heading</h1>
<section style="background-image:linear-gradient(#000,#000);padding:20px"><p style="color:#000">Text on a gradient is not measured</p></section>
<div style="position:relative;height:80px"><div style="position:absolute;inset:0;background:#000"></div><p style="position:relative;color:#fff;margin:0">Text over a sibling overlay is not measured</p></div>
<p style="color:#fff;text-shadow:0 0 2px #000">Shadowed text is not measured</p>
</body></html>"""


def test_dark_text_on_a_dark_background_is_recorded_as_a_contrast_failure(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(LOW_CONTRAST_PAGE, run_dir, tmp_path / "shots", run_dir)

    failures = result.viewports[0].contrast_failures
    assert [failure.text for failure in failures] == ["Card title"]
    assert failures[0].ratio < 3.0


def test_a_readable_page_records_no_contrast_failure(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(READABLE_PAGE, run_dir, tmp_path / "shots", run_dir)

    assert result.viewports[0].contrast_failures == []


OCCLUDED_TEXT_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div style="position:relative;height:200px">
<h1 style="margin:0;padding:20px;font-size:32px">Overlapped Heading Text</h1>
<div style="position:absolute;inset:0;background:#204060"></div>
</div>
</body></html>"""
SIDE_BY_SIDE_COLUMNS_PAGE = """<!DOCTYPE html><html><body style="margin:0;display:flex">
<div style="width:50%;padding:20px">Left column text content here</div>
<div style="width:50%;padding:20px;background:#eee">Right column text content here</div>
</body></html>"""
STACKED_BLOCKS_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div style="height:150px;padding:20px">First block text</div>
<div style="height:150px;padding:20px;background:#eee">Second block text</div>
</body></html>"""
TEXT_ON_OWN_BACKGROUND_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div style="background:#123456;padding:40px"><p style="color:#fff;margin:0">Text with its own background color</p></div>
</body></html>"""


def test_a_card_absolutely_positioned_over_a_heading_is_detected_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(OCCLUDED_TEXT_PAGE, run_dir, tmp_path / "shots", run_dir)

    occluded = result.viewports[0].occluded_text_elements
    assert [element.selector for element in occluded] == ["h1"]
    assert occluded[0].occluder != "h1"


def test_side_by_side_columns_are_not_reported_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(SIDE_BY_SIDE_COLUMNS_PAGE, run_dir, tmp_path / "shots", run_dir)

    assert result.viewports[0].occluded_text_elements == []


def test_stacked_blocks_are_not_reported_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(STACKED_BLOCKS_PAGE, run_dir, tmp_path / "shots", run_dir)

    assert result.viewports[0].occluded_text_elements == []


def test_text_on_its_own_background_is_not_reported_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(TEXT_ON_OWN_BACKGROUND_PAGE, run_dir, tmp_path / "shots", run_dir)

    assert result.viewports[0].occluded_text_elements == []


BELOW_THE_FOLD_OCCLUDED_PAGE = """<!DOCTYPE html><html><head><style>{root_css}</style></head><body style="margin:0">
<div style="height:1600px"></div>
<div style="position:relative;height:200px">
<h1 style="margin:0;padding:20px;font-size:32px">Overlapped Heading Text</h1>
<div style="position:absolute;inset:0;background:#204060"></div>
</div>
<div style="height:800px"></div>
</body></html>"""


@pytest.mark.parametrize("root_css", ["", "html{scroll-behavior:smooth}"])
def test_occlusion_below_the_fold_is_detected_even_with_smooth_scrolling(run_dir: Path, tmp_path: Path, root_css: str) -> None:
    html = BELOW_THE_FOLD_OCCLUDED_PAGE.replace("{root_css}", root_css)
    result = _evaluate(html, run_dir, tmp_path / "shots", run_dir)

    assert [element.selector for element in result.viewports[0].occluded_text_elements] == ["h1"]


FIXED_HEADER_OVER_HERO_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div style="position:fixed;top:0;left:0;right:0;height:80px;background:#204060"></div>
<h1 style="margin:0;padding:20px;font-size:32px">Hero Title Text</h1>
</body></html>"""
FIXED_HEADER_SCROLLED_CONTENT_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<div style="position:fixed;top:0;left:0;right:0;height:500px;background:#204060"></div>
<div style="height:1200px"></div>
<h1 style="margin:0;padding:20px;font-size:32px">Scrolled Under Heading</h1>
<div style="height:1200px"></div>
</body></html>"""
SKIP_LINK_UNDER_FIXED_HEADER_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<a href="#main" style="position:absolute;width:32px;height:16px;padding:8px 16px;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap">Skip to main content</a>
<div style="position:fixed;top:0;left:0;right:0;height:80px;background:#204060"></div>
<main id="main" style="padding:100px 20px">Page content</main>
</body></html>"""
VISIBLE_ANCHOR_LINK_UNDER_FIXED_HEADER_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<a href="#top" style="position:absolute;top:8px;left:8px;font-size:20px">Back to top</a>
<div style="position:fixed;top:0;left:0;right:0;height:80px;background:#204060"></div>
<main id="top" style="padding:100px 20px">Page content</main>
</body></html>"""
CIRCLE_CLIPPED_ANCHOR_LINK_UNDER_FIXED_HEADER_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<a href="#top" style="position:absolute;top:8px;left:8px;font-size:20px;clip-path:circle(50%)">Back to top</a>
<div style="position:fixed;top:0;left:0;right:0;height:80px;background:#204060"></div>
<main id="top" style="padding:100px 20px">Page content</main>
</body></html>"""


def test_a_fixed_header_over_hero_text_at_scroll_zero_is_reported_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(FIXED_HEADER_OVER_HERO_PAGE, run_dir, tmp_path / "shots", run_dir)

    occluded = result.viewports[0].occluded_text_elements
    assert [element.selector for element in occluded] == ["h1"]


def test_a_fixed_header_over_content_reached_only_by_scrolling_is_not_reported(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(FIXED_HEADER_SCROLLED_CONTENT_PAGE, run_dir, tmp_path / "shots", run_dir)

    assert result.viewports[0].occluded_text_elements == []


def test_a_skip_link_under_a_fixed_header_is_not_reported_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(SKIP_LINK_UNDER_FIXED_HEADER_PAGE, run_dir, tmp_path / "shots", run_dir)

    assert result.viewports[0].occluded_text_elements == []


def test_a_visible_anchor_link_under_a_fixed_header_is_still_reported_as_occluded(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(VISIBLE_ANCHOR_LINK_UNDER_FIXED_HEADER_PAGE, run_dir, tmp_path / "shots", run_dir)

    occluded = result.viewports[0].occluded_text_elements
    assert [element.selector for element in occluded] == ["a"]


def test_a_circle_clipped_but_still_visible_anchor_link_under_a_fixed_header_is_reported(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(CIRCLE_CLIPPED_ANCHOR_LINK_UNDER_FIXED_HEADER_PAGE, run_dir, tmp_path / "shots", run_dir)

    occluded = result.viewports[0].occluded_text_elements
    assert [element.selector for element in occluded] == ["a"]


BROKEN_IMAGE_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<img src="assets/hero.png" alt="ok" width="100" height="60">
<img src="assets/missing.png" alt="broken" width="100" height="60">
</body></html>"""


def test_a_missing_image_is_recorded_and_a_working_image_is_not(run_dir: Path, tmp_path: Path) -> None:
    result = _evaluate(BROKEN_IMAGE_PAGE, run_dir, tmp_path / "shots", run_dir)

    broken = result.viewports[0].broken_images
    assert len(broken) == 1
    assert broken[0].endswith("missing.png")


def _dark_pixels(path: Path) -> int:
    source = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            return page.evaluate(
                """async (url) => {
                    const image = new Image();
                    image.src = url;
                    await image.decode();
                    const canvas = document.createElement('canvas');
                    canvas.width = image.naturalWidth;
                    canvas.height = image.naturalHeight;
                    const context = canvas.getContext('2d');
                    context.drawImage(image, 0, 0);
                    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
                    let dark = 0;
                    for (let i = 0; i < data.length; i += 4) {
                        if (data[i] < 80 && data[i + 1] < 80 && data[i + 2] < 80) dark++;
                    }
                    return dark;
                }""",
                source,
            )
        finally:
            browser.close()


def test_the_page_is_rendered_where_its_assets_resolve(run_dir: Path) -> None:
    """Screenshots of a page whose every image failed to load are not evidence."""
    result = _evaluate(PAGE, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert [error for error in viewport.console_errors if "ERR_FILE_NOT_FOUND" in error] == []
    assert Path(viewport.screenshot_path).is_file()


def test_rendering_from_the_temp_directory_alone_loses_the_assets(run_dir: Path) -> None:
    """The behaviour the asset_root argument exists to correct."""
    result = _evaluate(PAGE, run_dir, run_dir / ".browser_tmp", asset_root=None)
    viewport = result.viewports[0]
    assert any("ERR_FILE_NOT_FOUND" in error for error in viewport.console_errors)


def test_the_preview_file_does_not_survive_the_run(run_dir: Path) -> None:
    _evaluate(PAGE, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    assert not (run_dir / PREVIEW_NAME).exists()


def test_overflow_is_still_reported(run_dir: Path) -> None:
    wide = '<html><body style="margin:0"><div style="width:2000px">wide</div></body></html>'
    result = _evaluate(wide, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    assert result.viewports[0].horizontal_overflow


def test_an_offscreen_skip_link_does_not_create_a_false_vertical_gap(run_dir: Path) -> None:
    page = """<!DOCTYPE html><html><body style="margin:0">
    <a href="#main" style="position:absolute;top:-9999px">Skip to content</a>
    <h1 style="margin:0">Heading</h1>
    </body></html>"""
    result = _evaluate(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    assert result.viewports[0].vertical_gap_bands == []


def _evaluate_mobile(html: str, run_dir: Path, output_dir: Path, asset_root: Path | None):
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="mobile", width=390, height=844)])
    return BrowserEvaluator(config).evaluate(html=html, output_dir=output_dir, iteration=1, asset_root=asset_root)


def test_a_wide_element_at_mobile_width_is_reported_as_an_overflow_offender(run_dir: Path) -> None:
    wide = '<html><body style="margin:0"><div class="wide" style="width:600px">wide</div></body></html>'
    result = _evaluate_mobile(wide, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert viewport.horizontal_overflow
    assert len(viewport.overflow_elements) == 1
    assert viewport.overflow_elements[0].selector == "div.wide"


def test_a_fixed_offcanvas_menu_trapped_by_a_backdrop_filter_ancestor_reports_once(run_dir: Path) -> None:
    page = (
        '<html><body style="margin:0">'
        '<header class="site-header" style="backdrop-filter:blur(14px)">'
        '<nav class="nav-links" style="position:fixed;top:0;left:0;right:0;bottom:0;transform:translateX(100%)">'
        '<a href="#">One</a><a href="#">Two</a>'
        "</nav></header>"
        '<div class="wide" style="width:600px">A</div>'
        '<div class="wide" style="width:600px">B</div>'
        "</body></html>"
    )
    result = _evaluate_mobile(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    selectors = [element.selector for element in viewport.overflow_elements]
    assert selectors.count("nav.nav-links (position: fixed trapped by header.site-header backdrop-filter)") == 1
    assert selectors.count("div.wide") == 2


def test_an_untrapped_fixed_offcanvas_drawer_is_not_reported(run_dir: Path) -> None:
    page = (
        '<html><body style="margin:0">'
        '<nav class="nav-links" style="position:fixed;top:0;left:0;right:0;bottom:0;transform:translateX(100%)">'
        '<a href="#">One</a><a href="#">Two</a>'
        "</nav>"
        '<div class="wide" style="width:600px">A</div>'
        "</body></html>"
    )
    result = _evaluate_mobile(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    selectors = [element.selector for element in viewport.overflow_elements]
    assert viewport.horizontal_overflow
    assert selectors == ["div.wide"]


def test_an_overflowing_pseudo_element_is_attributed_to_its_host(run_dir: Path) -> None:
    page = """<!DOCTYPE html><html><head><style>
    body{margin:0}
    .hero{position:relative;height:40px}
    .hero::before{content:"";position:absolute;top:0;right:-50px;width:100px;height:20px;background:#000}
    </style></head><body><div class="hero"></div></body></html>"""
    result = _evaluate_mobile(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert viewport.horizontal_overflow
    assert viewport.overflow_elements and viewport.overflow_elements[0].selector == "div.hero::before"


def test_a_wide_child_clipped_by_an_overflow_x_ancestor_is_not_reported(run_dir: Path) -> None:
    clipped = (
        '<html><body style="margin:0">'
        '<div style="overflow-x:auto"><div class="wide" style="width:600px">wide</div></div>'
        "</body></html>"
    )
    result = _evaluate_mobile(clipped, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert not viewport.horizontal_overflow
    assert viewport.overflow_elements == []


def test_a_nowrap_flex_child_overflowing_past_a_body_overflow_x_hidden_is_still_reported(run_dir: Path) -> None:
    page = (
        '<html><body style="margin:0;overflow-x:hidden">'
        '<div class="footer-grid" style="display:flex;justify-content:space-between">'
        '<div>Brand</div>'
        '<div class="footer-links" style="display:flex;gap:1.5rem;white-space:nowrap">'
        "<a href=\"#\">Features</a><a href=\"#\">Pricing</a><a href=\"#\">hello@flowstack.example.com</a>"
        "</div></div></body></html>"
    )
    result = _evaluate_mobile(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert viewport.horizontal_overflow
    assert any(el.selector == "div.footer-links" for el in viewport.overflow_elements)


def test_a_full_bleed_background_image_with_a_narrow_text_box_extends_the_first_screen_reach(run_dir: Path) -> None:
    page = (
        '<html><body style="margin:0">'
        '<div style="width:100%;height:600px;background-image:linear-gradient(red,blue)">'
        '<div style="width:300px">hero text</div>'
        "</div>"
        "</body></html>"
    )
    result = _evaluate(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert viewport.first_screen_max_right is not None
    assert viewport.first_screen_max_right / viewport.viewport_width >= 0.55


def test_evaluation_screenshot_reveals_intersection_observer_sections(tmp_path: Path) -> None:
    untouched_html = tmp_path / "untouched.html"
    untouched = tmp_path / "untouched.png"
    untouched_html.write_text(REVEAL_PAGE, encoding="utf-8")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.goto(untouched_html.resolve().as_uri(), wait_until="networkidle")
            page.screenshot(path=str(untouched), full_page=True)
        finally:
            browser.close()

    result = _evaluate(REVEAL_PAGE, tmp_path, tmp_path / ".browser_tmp", asset_root=tmp_path)
    captured = Path(result.viewports[0].screenshot_path)

    assert _dark_pixels(untouched) > 0
    assert _dark_pixels(captured) > _dark_pixels(untouched) * 2.5


def test_reveal_pass_bounds_a_page_that_grows_on_scroll() -> None:
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(GROWING_REVEAL_PAGE)
            started = time.monotonic()
            revealed = reveal_animated_content(page, 6000)
        finally:
            browser.close()

    assert time.monotonic() - started < 2
    assert revealed >= 3


def test_reveal_pass_keeps_keyframe_animated_elements_visible_after_freeze() -> None:
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(FADE_UP_KEYFRAME_PAGE)
            page.wait_for_timeout(500)
            reveal_animated_content(page, 6000)
            opacity = page.eval_on_selector(".fade-up", "el => getComputedStyle(el).opacity")
        finally:
            browser.close()

    assert float(opacity) >= 0.95


HIDDEN_LAYER_PAGE = """<!DOCTYPE html><html><head><style>
body{margin:0}div{width:320px;height:180px;background:#102030}
#flow{opacity:0}#overlay{position:fixed;top:0;left:0;width:100%;height:100%;opacity:0;pointer-events:none}
#inert{opacity:0;pointer-events:none}#solid-fixed{position:fixed;top:0;left:0;opacity:0}
#glow{position:absolute;top:0;left:0;opacity:0;pointer-events:none}
</style></head><body><div id="flow"></div><div id="overlay"><a href="#" style="display:block;width:100px;height:40px">Menu</a></div><div id="inert"></div><div id="solid-fixed"></div><div id="glow"></div></body></html>"""


def test_reveal_pass_forces_in_flow_sections_but_not_hidden_ui_layers() -> None:
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(HIDDEN_LAYER_PAGE)
            revealed = reveal_animated_content(page, 6000)
            opacities = {
                name: float(page.eval_on_selector(f"#{name}", "el => getComputedStyle(el).opacity"))
                for name in ("flow", "overlay", "inert", "solid-fixed", "glow")
            }
        finally:
            browser.close()

    assert revealed == 2
    assert opacities["flow"] == 1
    assert opacities["overlay"] == 0
    assert opacities["inert"] == 1
    assert opacities["solid-fixed"] == 0
    assert opacities["glow"] == 0


def test_reveal_pass_returns_to_the_top_on_a_smooth_scrolling_page() -> None:
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content('<style>html{scroll-behavior:smooth}</style><div style="height:20000px"></div>')
            reveal_animated_content(page, 6000)
            scroll_y = page.evaluate("window.scrollY")
        finally:
            browser.close()

    assert scroll_y == 0


def test_evaluation_metrics_match_the_revealed_screenshot_geometry(tmp_path: Path) -> None:
    result = _evaluate(TRANSLATED_REVEAL_PAGE, tmp_path, tmp_path / ".browser_tmp", asset_root=tmp_path)
    viewport = result.viewports[0]

    assert not viewport.horizontal_overflow
    assert viewport.scroll_width == viewport.viewport_width


class _FakeRoute:
    def __init__(self) -> None:
        self.aborted = False
        self.continued = False

    def abort(self, error_code: str | None = None) -> None:
        self.aborted = True

    def continue_(self) -> None:
        self.continued = True


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


def test_route_handler_blocks_network_and_out_of_dir_file_requests(tmp_path: Path) -> None:
    allowed = tmp_path / "site"
    allowed.mkdir()
    (allowed / "hero.png").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("SECRET", encoding="utf-8")
    handler = _make_route_handler(allowed.resolve())

    http_route = _FakeRoute()
    handler(http_route, _FakeRequest("http://example.com/x.png"))
    assert http_route.aborted and not http_route.continued

    outside_route = _FakeRoute()
    handler(outside_route, _FakeRequest(outside.resolve().as_uri()))
    assert outside_route.aborted and not outside_route.continued

    inside_route = _FakeRoute()
    handler(inside_route, _FakeRequest((allowed / "hero.png").resolve().as_uri()))
    assert inside_route.continued and not inside_route.aborted


def test_route_handler_blocks_a_non_local_file_netloc(tmp_path: Path) -> None:
    allowed = tmp_path / "site"
    allowed.mkdir()
    (allowed / "hero.png").write_text("x", encoding="utf-8")
    handler = _make_route_handler(allowed.resolve())

    unc_route = _FakeRoute()
    handler(unc_route, _FakeRequest("file://evil-host" + (allowed / "hero.png").resolve().as_posix()))
    assert unc_route.aborted and not unc_route.continued


def test_route_handler_aborts_instead_of_raising_on_a_nul_byte_path(tmp_path: Path) -> None:
    allowed = tmp_path / "site"
    allowed.mkdir()
    handler = _make_route_handler(allowed.resolve())

    route = _FakeRoute()
    handler(route, _FakeRequest("file:///" + allowed.resolve().as_posix().lstrip("/") + "/evil%00.png"))
    assert route.aborted and not route.continued


def test_one_viewport_failing_does_not_discard_the_others(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_goto = pw.Page.goto

    def flaky_goto(self, url, **kwargs):
        if self.viewport_size and self.viewport_size["width"] == 999:
            raise RuntimeError("boom")
        return original_goto(self, url, **kwargs)

    monkeypatch.setattr(pw.Page, "goto", flaky_goto)
    config = BrowserConfig(enabled=True, viewports=[
        BrowserViewport(name="broken", width=999, height=600),
        BrowserViewport(name="desktop", width=800, height=600),
    ])
    result = BrowserEvaluator(config).evaluate(html=PAGE, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert [viewport.name for viewport in result.viewports] == ["desktop"]
    assert result.failed_viewports == ["broken"]


def test_the_real_browser_is_blocked_from_fetching_outside_the_page_root(tmp_path: Path) -> None:
    """Playwright's context.route is asserted, end to end, to actually stop
    both a file:// subresource outside the allowed directory and a plain
    http image, from a file:// page - not just the handler in isolation."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\n")
    run_dir = tmp_path / "run"
    html = (
        f'<html><body><img id="leak" src="{secret.resolve().as_uri()}">'
        '<img id="net" src="http://example.invalid/x.png"></body></html>'
    )
    result = _evaluate(html, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]
    aborted = [error for error in viewport.console_errors if "ERR_FAILED" in error or "ERR_ABORTED" in error or "ERR_BLOCKED_BY_CLIENT" in error]
    assert len(aborted) >= 2


def test_path_from_file_url_rejects_a_unc_style_path() -> None:
    with pytest.raises(ValueError):
        _path_from_file_url("file:////evil/share/x")
    with pytest.raises(ValueError):
        _path_from_file_url("file:///%5C%5Cevil%5Cshare%5Cx")


def test_a_websocket_from_the_page_never_reaches_a_real_server(run_dir: Path) -> None:
    """A page could otherwise open ws://127.0.0.1:<port> straight past context.route."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    connected = threading.Event()

    def accept_once():
        server.settimeout(5)
        try:
            conn, _ = server.accept()
            connected.set()
            conn.close()
        except OSError:
            pass
        finally:
            server.close()

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    html = f'<html><body><script>new WebSocket("ws://127.0.0.1:{port}/");</script></body></html>'

    _evaluate(html, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    thread.join(timeout=6)

    assert not connected.is_set()


def test_a_cached_framework_resolves_locally(run_dir: Path, tmp_path: Path) -> None:
    """The rewrite lets a cached CDN framework script run instead of aborting."""
    cache_dir = tmp_path / "cache"
    js_url = "https://cdn.example.test/framework.js"
    frameworks = FrameworkConfig(js=[Framework(name="Test", cdn=js_url, tag="script")])
    cached = cached_path_for_url(js_url, ".js", cache_dir)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("window.__testFrameworkLoaded = true;", encoding="utf-8")
    html = f'<html><body><script src="{js_url}"></script><script>if (!window.__testFrameworkLoaded) {{ throw new Error("framework not loaded"); }}</script></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    result = BrowserEvaluator(config, frameworks=frameworks, cache_dir=cache_dir).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert result.viewports[0].page_errors == []
    assert result.blocked_external_resources == []
    assert not list(run_dir.glob(f"{FRAMEWORK_CACHE_DIRNAME}_*"))


def test_a_localization_failure_still_cleans_up_the_cache_dir(run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mid-copy failure must not leave a partial _frameworks_cache in the saved run dir."""
    cache_dir = tmp_path / "cache"
    js_url = "https://cdn.example.test/framework.js"
    frameworks = FrameworkConfig(js=[Framework(name="Test", cdn=js_url, tag="script")])
    cached = cached_path_for_url(js_url, ".js", cache_dir)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("window.__testFrameworkLoaded = true;", encoding="utf-8")
    html = f'<html><body><script src="{js_url}"></script></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])

    def failing_copyfile(src, dst, *args, **kwargs):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        raise OSError("disk full")

    monkeypatch.setattr("wra.browser.evaluator.shutil.copyfile", failing_copyfile)
    with pytest.raises(OSError):
        BrowserEvaluator(config, frameworks=frameworks, cache_dir=cache_dir).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert not list(run_dir.glob(f"{FRAMEWORK_CACHE_DIRNAME}_*"))


def test_a_preexisting_cache_named_directory_survives_evaluation(run_dir: Path, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    js_url = "https://cdn.example.test/framework.js"
    frameworks = FrameworkConfig(js=[Framework(name="Test", cdn=js_url, tag="script")])
    cached = cached_path_for_url(js_url, ".js", cache_dir)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("window.__testFrameworkLoaded = true;", encoding="utf-8")
    html = f'<html><body><script src="{js_url}"></script></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    user_frameworks_file = run_dir / FRAMEWORK_CACHE_DIRNAME / "logo.png"
    user_http_file = run_dir / HTTP_CACHE_DIRNAME / "font.woff2"
    for user_file in (user_frameworks_file, user_http_file):
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_bytes(b"user asset")
    BrowserEvaluator(config, frameworks=frameworks, cache_dir=cache_dir).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert user_frameworks_file.read_bytes() == b"user asset"
    assert user_http_file.read_bytes() == b"user asset"
    assert not list(run_dir.glob(f"{FRAMEWORK_CACHE_DIRNAME}_*"))
    assert not list(run_dir.glob(f"{HTTP_CACHE_DIRNAME}_*"))


def test_an_uncached_catalog_url_is_downloaded_automatically(run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No prefetched copy: it is downloaded on the fly instead of being left blocked."""
    from wra.browser import evaluator as evaluator_module

    cache_dir = tmp_path / "cache"
    js_url = "https://cdn.example.test/framework.js"
    frameworks = FrameworkConfig(js=[Framework(name="Test", cdn=js_url, tag="script")])
    calls = []

    def fake_ensure_cached_path_for_url(url, extension, cache_dir):
        calls.append(url)
        dest = cached_path_for_url(url, extension, cache_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("window.__testFrameworkLoaded = true;", encoding="utf-8")
        return dest

    monkeypatch.setattr(evaluator_module, "ensure_cached_path_for_url", fake_ensure_cached_path_for_url)
    html = f'<html><body><script src="{js_url}"></script><script>if (!window.__testFrameworkLoaded) {{ throw new Error("framework not loaded"); }}</script></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    result = BrowserEvaluator(config, frameworks=frameworks, cache_dir=cache_dir).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert calls == [js_url]
    assert result.viewports[0].page_errors == []


def test_a_download_failure_fails_fast_naming_the_url(run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A catalog URL that fails to download must raise instead of silently evaluating a broken page."""
    from wra.browser import evaluator as evaluator_module

    cache_dir = tmp_path / "cache"
    js_url = "https://cdn.example.test/framework.js"
    frameworks = FrameworkConfig(js=[Framework(name="Test", cdn=js_url, tag="script")])

    def fake_ensure_cached_path_for_url(url, extension, cache_dir):
        raise RuntimeError(f"Failed to download framework asset for browser evaluation: {url} (boom)")

    monkeypatch.setattr(evaluator_module, "ensure_cached_path_for_url", fake_ensure_cached_path_for_url)
    html = f'<html><body><script src="{js_url}"></script></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    with pytest.raises(RuntimeError, match=re.escape(js_url)):
        BrowserEvaluator(config, frameworks=frameworks, cache_dir=cache_dir).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)


def test_an_uncataloged_external_script_is_recorded_as_blocked(run_dir: Path) -> None:
    """A CDN URL that isn't in the framework catalog is still blocked, but now surfaced instead of silently swallowed."""
    external_url = "https://cdn.jsdelivr.net/npm/some-lib@1.0/dist/lib.min.js"
    html = f'<html><body><script src="{external_url}"></script></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    result = BrowserEvaluator(config).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert result.blocked_external_resources == [external_url]


def test_the_gsap_scrolltrigger_plugin_is_cached_alongside_gsap_core() -> None:
    frameworks = FrameworkConfig(js=[Framework(name="GSAP", cdn="https://cdn.jsdelivr.net/npm/gsap@3.15/dist/gsap.min.js", tag="script")])

    assert iter_framework_urls(frameworks) == [
        ("https://cdn.jsdelivr.net/npm/gsap@3.15/dist/gsap.min.js", ".js"),
        ("https://cdn.jsdelivr.net/npm/gsap@3.15/dist/ScrollTrigger.min.js", ".js"),
    ]


def test_the_bootstrap_js_bundle_is_cached_alongside_bootstrap_css() -> None:
    frameworks = FrameworkConfig(css=[Framework(name="Bootstrap", cdn="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css", tag="link")])

    assert iter_framework_urls(frameworks) == [
        ("https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css", ".css"),
        ("https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js", ".js"),
    ]


def test_the_saved_output_html_keeps_the_original_cdn_url(run_dir: Path, tmp_path: Path) -> None:
    """The rewrite must only ever touch the browser-evaluation copy of the page."""
    cache_dir = tmp_path / "cache"
    css_url = "https://cdn.example.test/framework.css"
    frameworks = FrameworkConfig(css=[Framework(name="Test", cdn=css_url, tag="link")])
    cached = cached_path_for_url(css_url, ".css", cache_dir)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("h1 { color: red; }", encoding="utf-8")
    html = f'<html><head><link rel="stylesheet" href="{css_url}"></head><body><h1>Hi</h1></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    BrowserEvaluator(config, frameworks=frameworks, cache_dir=cache_dir).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert css_url in html
    assert not list(run_dir.glob(f"{FRAMEWORK_CACHE_DIRNAME}_*"))
    assert not (run_dir / PREVIEW_NAME).exists()


def test_a_cached_google_font_resolves_locally_without_network(run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rewrite lets a cached web font load instead of aborting - no real
    network call is made; fetch_cached is stubbed to serve pre-seeded files."""
    from wra.browser import evaluator as evaluator_module

    css_url = "https://fonts.googleapis.com/css2?family=Test+Font&display=swap"
    font_url = "https://fonts.gstatic.com/s/testfont/v1/abc.woff2"
    seeded_css = tmp_path / "seeded.css"
    seeded_css.write_text(f"@font-face{{font-family:'Test Font';src:url({font_url}) format('woff2');}}", encoding="utf-8")
    seeded_font = tmp_path / "seeded.woff2"
    seeded_font.write_bytes(b"\x00fake-font-bytes")

    def fake_fetch_cached(url, cache_dir, **kwargs):
        if url == css_url:
            return seeded_css
        if url == font_url:
            return seeded_font
        return None

    monkeypatch.setattr(evaluator_module, "fetch_cached", fake_fetch_cached)
    html = f'<html><head><link rel="stylesheet" href="{css_url}"></head><body><h1>Hi</h1></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    result = BrowserEvaluator(config).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)

    assert result.viewports[0].page_errors == []
    assert result.blocked_external_resources == []
    assert not list(run_dir.glob(f"{evaluator_module.HTTP_CACHE_DIRNAME}_*"))


def test_css_url_count_beyond_the_per_page_cap_is_not_all_fetched(run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A page emitting far more distinct family URLs than the cap must only
    trigger a bounded number of fetches (CWE-770)."""
    from wra.browser import evaluator as evaluator_module

    seeded_css = tmp_path / "seeded.css"
    seeded_css.write_text("body{font-family:'Test Font'}", encoding="utf-8")
    calls = []

    def fake_fetch_cached(url, cache_dir, **kwargs):
        calls.append(url)
        return seeded_css

    monkeypatch.setattr(evaluator_module, "fetch_cached", fake_fetch_cached)
    links = "".join(
        f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Font{i}">'
        for i in range(evaluator_module.MAX_CSS_URLS_PER_PAGE + 20)
    )
    html = f"<html><head>{links}</head><body><h1>Hi</h1></body></html>"
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    BrowserEvaluator(config).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)

    assert len(calls) == evaluator_module.MAX_CSS_URLS_PER_PAGE


def test_web_font_localization_can_be_disabled(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wra.browser import evaluator as evaluator_module

    calls = []
    monkeypatch.setattr(evaluator_module, "fetch_cached", lambda *a, **k: calls.append(a) or None)
    css_url = "https://fonts.googleapis.com/css2?family=Test+Font"
    html = f'<html><head><link rel="stylesheet" href="{css_url}"></head><body><h1>Hi</h1></body></html>'
    config = BrowserConfig(enabled=True, localize_web_fonts=False, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    BrowserEvaluator(config).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert calls == []


def test_an_unresolvable_font_url_still_aborts_like_today(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache-miss/fetch-failure (stubbed here, never a real network call)
    must leave the URL untouched so the route handler aborts it as before."""
    from wra.browser import evaluator as evaluator_module

    monkeypatch.setattr(evaluator_module, "fetch_cached", lambda *a, **k: None)
    css_url = "https://fonts.googleapis.com/css2?family=Nonexistent"
    html = f'<html><head><link rel="stylesheet" href="{css_url}"></head><body><h1>Hi</h1></body></html>'
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    result = BrowserEvaluator(config).evaluate(html=html, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)
    assert result.failed_viewports == []
    assert not list(run_dir.glob(f"{evaluator_module.HTTP_CACHE_DIRNAME}_*"))


def test_all_viewports_failing_raises(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def always_fail(self, url, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pw.Page, "goto", always_fail)
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    with pytest.raises(RuntimeError):
        BrowserEvaluator(config).evaluate(html=PAGE, output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)


def _narrow_section(width: int) -> str:
    return f'<section style="min-height:200px"><div style="max-width:{width}px;margin:0 auto"><p>{"Lorem ipsum dolor sit amet consectetur. " * 30}</p></div></section>'


def _wide_text_page(width: int, extra: str = "") -> str:
    return f'<!DOCTYPE html><html><body style="margin:0">{extra}<main>{_narrow_section(width)}{_narrow_section(width)}</main></body></html>'


def test_a_narrow_text_column_is_measured_below_the_threshold(run_dir: Path) -> None:
    result = _evaluate(_wide_text_page(420), run_dir, run_dir / ".browser_tmp", asset_root=run_dir)

    assert 300 <= result.wide_text_width_median <= 440


def test_a_wide_text_layout_is_measured_above_the_threshold(run_dir: Path) -> None:
    result = _evaluate(_wide_text_page(1200), run_dir, run_dir / ".browser_tmp", asset_root=run_dir)

    assert result.wide_text_width_median >= 1000


def test_nav_and_fixed_elements_do_not_widen_the_text_width(run_dir: Path) -> None:
    ends = '<span>Left end</span><span>Right end</span>'
    nav = f'<nav style="display:flex;justify-content:space-between">{ends}</nav>'
    fixed = f'<div style="position:fixed;bottom:0;left:0;right:0;display:flex;justify-content:space-between">{ends}</div>'
    section = f'<section style="min-height:200px">{nav}{fixed}<div style="max-width:420px;margin:0 auto"><p>{"Lorem ipsum dolor sit amet consectetur. " * 30}</p></div></section>'
    page = f'<!DOCTYPE html><html><body style="margin:0"><main>{section}{section}</main></body></html>'
    result = _evaluate(page, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)

    assert 300 <= result.wide_text_width_median <= 440


def test_a_page_without_main_and_without_sections_measures_nothing(run_dir: Path) -> None:
    result = _evaluate("<html><body><p>hi</p></body></html>", run_dir, run_dir / ".browser_tmp", asset_root=run_dir)

    assert result.wide_text_width_median is None


def test_the_wide_measurement_does_not_change_the_viewport_screenshots_or_metrics(run_dir: Path) -> None:
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600), BrowserViewport(name="mobile", width=400, height=800)])
    result = BrowserEvaluator(config).evaluate(html=_wide_text_page(420), output_dir=run_dir / ".browser_tmp", iteration=1, asset_root=run_dir)

    assert [(viewport.viewport_width, viewport.scroll_width) for viewport in result.viewports] == [(800, 800), (400, 400)]
    assert all(viewport.screenshot_path and Path(viewport.screenshot_path).exists() for viewport in result.viewports)
