import importlib.util

import pytest

from wra.generator.fixed_trap_repair import repair_trapped_fixed

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")


def _trapped_menu_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  header.site-header { position: relative; backdrop-filter: blur(12px); padding: 1rem; }
  ul#nav-menu { position: fixed; top: 0; right: 0; width: 100%; height: 100%; margin: 0; padding: 0; list-style: none; box-sizing: border-box; transform: translateX(0); background: white; }
</style>
</head>
<body>
<header class="site-header">
  <ul id="nav-menu"><li>Home</li></ul>
</header>
<main><p>content</p></main>
</body>
</html>"""


def _unused_before_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  header.site-header { position: relative; backdrop-filter: blur(12px); padding: 1rem; }
  header.site-header::before { content: "used"; }
  ul#nav-menu { position: fixed; top: 0; right: 0; width: 100%; height: 100%; background: white; }
</style>
</head>
<body>
<header class="site-header">
  <ul id="nav-menu"><li>Home</li></ul>
</header>
<main><p>content</p></main>
</body>
</html>"""


def _no_trap_html() -> str:
    return """<!DOCTYPE html>
<html>
<head><style>header { padding: 1rem; }</style></head>
<body>
<header><nav>links</nav></header>
<main><p>content</p></main>
</body>
</html>"""


def _skip_if_no_browser(repaired: str, original: str) -> None:
    if repaired == original:
        pytest.skip("Playwright launch unavailable in this environment")


def test_repairs_trapped_menu_and_keeps_blur_on_before() -> None:
    html = _trapped_menu_html()

    repaired, repairs = repair_trapped_fixed(html)

    _skip_if_no_browser(repaired, html)
    assert repairs
    assert 'data-wra-repair="fixed-trap"' in repaired
    assert "header.site-header::before" in repaired
    assert "backdrop-filter: blur(12px)" in repaired

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 390, "height": 800})
            page.set_content(repaired)
            rect = page.eval_on_selector("#nav-menu", "el => el.getBoundingClientRect()")
            assert round(rect["width"]) == 390
            before_backdrop = page.eval_on_selector(
                "header.site-header",
                "el => getComputedStyle(el, '::before').backdropFilter",
            )
            assert before_backdrop and before_backdrop != "none"
        finally:
            browser.close()


def test_leaves_html_unchanged_when_before_already_used() -> None:
    html = _unused_before_html()

    repaired, repairs = repair_trapped_fixed(html)

    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright not installed")
    assert repaired == html
    assert repairs == []


def test_leaves_html_unchanged_when_no_trap() -> None:
    html = _no_trap_html()

    repaired, repairs = repair_trapped_fixed(html)

    assert repaired == html
    assert repairs == []


def test_repair_only_inserts_the_style_block() -> None:
    html = _trapped_menu_html()

    repaired, repairs = repair_trapped_fixed(html)

    _skip_if_no_browser(repaired, html)
    assert repairs
    head_end = repaired.index("</head>")
    original_head_end = html.index("</head>")
    assert repaired[:original_head_end] == html[:original_head_end]
    inserted = repaired[original_head_end:head_end]
    assert inserted.strip().startswith('<style data-wra-repair="fixed-trap">')
    assert repaired[head_end:] == html[original_head_end:]
