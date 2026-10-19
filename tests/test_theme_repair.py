import importlib.util

import pytest

from wra.browser.contrast import CONTRAST_CANDIDATES_SCRIPT, contrast_failures
from wra.generator.theme_repair import repair_overridden_root_theme

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")

_FRAMEWORK = "body { background-color: #fff; color: #212529; }"


def _page(style: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<link rel="stylesheet" href="framework.css">
<style>
  {style}
</style>
</head>
<body>
{body}
</body>
</html>"""


_DARK_THEME = "html { background-color: #0f172a; color: #f8fafc; } body { margin: 0; font-size: 16px; } .band { background-color: #0f172a; padding: 16px; }"
_DARK_BODY = '<section class="band"><h1>Welcome home</h1></section><p>Plain paragraph</p>'


def _render(tmp_path, html: str):
    from playwright.sync_api import sync_playwright

    (tmp_path / "framework.css").write_text(_FRAMEWORK, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            found = []
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 800})
                page.goto((tmp_path / "page.html").as_uri())
                found.extend(contrast_failures(page.evaluate(CONTRAST_CANDIDATES_SCRIPT)))
            body = page.evaluate("() => { const s = getComputedStyle(document.body); return [s.backgroundColor, s.color, getComputedStyle(document.documentElement).color]; }")
            return found, body
        finally:
            browser.close()


def _repair(tmp_path, html: str):
    (tmp_path / "framework.css").write_text(_FRAMEWORK, encoding="utf-8")
    return repair_overridden_root_theme(html, asset_root=tmp_path)


def test_dark_root_theme_under_framework_body_is_restored(tmp_path):
    html = _page(_DARK_THEME, _DARK_BODY)
    failures, _ = _render(tmp_path, html)
    assert failures
    repaired, repairs = _repair(tmp_path, html)
    assert len(repairs) == 1
    assert repairs[0].startswith("html #0f172a/#f8fafc under body #ffffff/#212529, low-contrast ")
    assert repaired.count('<style data-wra-repair="theme">') == 1
    failures, (background, color, root_color) = _render(tmp_path, repaired)
    assert failures == []
    assert background == "rgba(0, 0, 0, 0)"
    assert color == root_color


def test_near_white_root_is_left_alone(tmp_path, caplog):
    html = _page("html { background-color: #f8f9fa; color: #1a1a1a; } body { margin: 0; }", "<h1>Welcome home</h1>")
    assert _repair(tmp_path, html) == (html, [])
    assert "Theme repair did not verify" not in caplog.text


def test_author_styled_body_background_is_left_alone(tmp_path):
    html = _page(_DARK_THEME + " body { background: #fff; }", _DARK_BODY)
    assert _repair(tmp_path, html) == (html, [])


def test_print_only_body_rule_does_not_block_the_repair(tmp_path):
    html = _page(_DARK_THEME + " @media print { body { background: #fff; color: #000; } }", _DARK_BODY)
    repaired, repairs = _repair(tmp_path, html)
    assert len(repairs) == 1
    block = repaired.split('<style data-wra-repair="theme">')[1].split("</style>")[0]
    assert "background-color:transparent;color:inherit" in block


def test_repair_is_idempotent(tmp_path):
    repaired, repairs = _repair(tmp_path, _page(_DARK_THEME, _DARK_BODY))
    assert repairs
    assert _repair(tmp_path, repaired) == (repaired, [])


def test_repair_that_improves_nothing_is_dropped(tmp_path):
    html = _page(_DARK_THEME, '<main style="background-color: #fff; color: #111; padding: 16px;"><h1>Welcome home</h1></main>')
    assert _repair(tmp_path, html) == (html, [])


def test_author_body_colour_is_kept(tmp_path):
    html = _page(_DARK_THEME + " body { color: #f8fafc; }", "<p>Plain paragraph</p>")
    repaired, repairs = _repair(tmp_path, html)
    assert len(repairs) == 1
    block = repaired.split('<style data-wra-repair="theme">')[1].split("</style>")[0]
    assert "background-color:transparent" in block
    assert "color:inherit" not in block
