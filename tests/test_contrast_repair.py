import importlib.util

import pytest

from wra.browser.contrast import CONTRAST_CANDIDATES_SCRIPT, contrast_failures
from wra.generator.contrast_repair import repair_low_contrast

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")


def _page(style: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; font-size: 16px; }}
  {style}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _failures(html: str) -> list:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            found = []
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 800})
                page.set_content(html)
                found.extend(contrast_failures(page.evaluate(CONTRAST_CANDIDATES_SCRIPT)))
            return found
        finally:
            browser.close()


def _button_html() -> str:
    return _page(
        ".btn { display: inline-block; padding: 12px 24px; background: #5b6ee1; color: #ffffff; }",
        '<main><a class="btn" href="#">Start Free Trial</a></main>',
    )


def test_white_text_on_accent_button_darkens_the_button_background():
    html = _button_html()
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 1
    assert "'Start Free Trial' #ffffff/#5b6ee1 4.41->" in repairs[0]
    assert "(bg #" in repairs[0]
    assert 'data-wra-contrast="1"' in repaired
    assert repaired.count('<style data-wra-repair="contrast">') == 1
    assert "data-wra-i" not in repaired


def test_white_text_on_oklch_accent_button_darkens_the_button_background():
    html = _page(
        ".btn { display: inline-block; padding: 12px 24px; background: oklch(0.65 0.15 270); color: #ffffff; }",
        '<main><a class="btn" href="#">Start Free Trial</a></main>',
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 1
    assert "(bg #" in repairs[0]


def test_muted_text_on_dark_background_is_lightened():
    html = _page("body { background: #0f172a; } p { color: #64748b; }", "<main><p>Muted caption text</p></main>")
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert "(fg #" in repairs[0]


def test_invisible_text_matching_its_background_is_repaired():
    html = _page(
        ".brand-block { background: #2c1a14; padding: 12px; } .brand { color: #2c1a14; }",
        '<main><div class="brand-block"><span class="brand">Brand Name</span></div></main>',
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 1


def test_large_text_uses_the_lower_threshold():
    html = _page("h1 { font-size: 32px; color: #888888; }", "<main><h1>Big Heading</h1></main>")
    assert _failures(html) == []
    assert repair_low_contrast(html) == (html, [])


def test_text_large_only_on_desktop_is_repaired_to_the_stricter_mobile_threshold():
    html = _page(
        "body { background: #f5f7fa; } h3 { font-size: 26px; font-weight: 600; color: #90a1b9; } @media (max-width: 600px) { h3 { font-size: 20px; } }",
        "<main><h3>Phone</h3></main>",
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 1


def test_passing_page_is_returned_unchanged():
    html = _page("p { color: #111111; }", "<main><p>Readable text</p></main>")
    assert repair_low_contrast(html) == (html, [])


def test_text_on_ancestor_background_with_white_foreground_is_skipped():
    html = _page(
        ".hero { background: #5b6ee1; padding: 20px; } .hero p { color: #ffffff; }",
        '<main><div class="hero"><p>White on an ancestor accent</p></div></main>',
    )
    assert _failures(html)
    assert repair_low_contrast(html) == (html, [])


def test_repeated_failures_share_one_repair_entry_and_only_marked_elements_change():
    html = _page(
        "p { color: #999999; } p.ok { color: #111111; }",
        "<main><p>Price one</p><p>Price two</p><p>Price three</p><p class=\"ok\">Fine text</p></main>",
    )
    repaired, repairs = repair_low_contrast(html)
    assert len(repairs) == 1
    assert repairs[0].endswith("x3")
    assert repaired.count("data-wra-contrast=") == 3 + 1
    assert '<p class="ok">' in repaired
    assert _failures(repaired) == []


def test_repair_is_idempotent():
    repaired, repairs = repair_low_contrast(_button_html())
    assert repairs
    assert repair_low_contrast(repaired) == (repaired, [])


def test_inline_styles_are_overridden():
    html = _page(
        "body { background: #1e293b; }",
        '<main><span style="color: #64748B;">Inline muted</span>'
        '<div style="position: relative; background: #8b5cf6; color: white; display: inline-block; padding: 4px">Inline badge</div></main>',
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 2
    assert "!important" in repaired


def test_rule_does_not_override_hover_styles():
    repaired, _ = repair_low_contrast(_button_html())
    assert '[data-wra-contrast="1"][data-wra-contrast]:not(:hover)' in repaired


def test_recolouring_a_parent_that_would_break_a_dark_child_is_dropped():
    html = _page(
        ".parent { background: #ffffff; color: #999999; } .dark { background: #111111; padding: 4px; }",
        '<main><div class="parent">Parent text <span class="dark">Child text</span></div></main>',
    )
    before_texts = {failure.text for failure in _failures(html)}
    assert "Parent text" in before_texts
    repaired, repairs = repair_low_contrast(html)
    assert "Child text" not in {failure.text for failure in _failures(repaired)}
    assert (repaired, repairs) == (html, [])


def test_render_exception_returns_input_unchanged(monkeypatch):
    def boom(page, html):
        raise RuntimeError("render failed")

    monkeypatch.setattr("wra.generator.contrast_repair._find_failures", boom)
    html = _button_html()
    assert repair_low_contrast(html) == (html, [])


def test_uppercase_tags_and_gt_inside_attribute_values():
    html = (
        "<!DOCTYPE html><HTML><HEAD><STYLE>body{margin:0}</STYLE></HEAD>"
        '<BODY><MAIN><P DATA-X="a>b" STYLE="color:#999999">Faint text</P></MAIN></BODY></HTML>'
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert len(repairs) == 1
    assert _failures(repaired) == []
    assert '<P data-wra-contrast="1" DATA-X="a>b"' in repaired
    assert repaired.index('<style data-wra-repair="contrast">') < repaired.index("</BODY>")


def test_important_class_colour_is_overridden():
    html = _page(
        "body { background: #222222; } .text-muted { color: #212427 !important; }",
        '<main><p class="text-muted">Copyright notice</p></main>',
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 1


def test_black_text_on_dark_ancestor_background_is_lightened():
    html = _page(
        ".band { background: #003366; padding: 20px; } .band p { color: #000000; }",
        '<main><div class="band"><p>Research Areas</p></div></main>',
    )
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert "(fg #" in repairs[0]


def test_oklch_low_contrast_page_is_repaired():
    html = _page("body { background: oklch(0.208 0.042 265.755); } p { color: oklch(0.554 0.041 257.417); }", "<main><p>Muted caption text</p></main>")
    assert _failures(html)
    repaired, repairs = repair_low_contrast(html)
    assert _failures(repaired) == []
    assert len(repairs) == 1
    assert "(fg #" in repairs[0]


@pytest.fixture
def probe_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("use_asset_root", [False, True])
def test_repair_never_requests_urls_found_in_generated_html(probe_server, tmp_path, use_asset_root):
    base, hits = probe_server
    html = _page(
        ".low { color: #bbb; background: #fff; }",
        f'<script src="{base}/repair-probe.js"></script><img src="{base}/repair-probe.png"><p class="low">hello</p>',
    )

    repaired, repairs = repair_low_contrast(html, tmp_path if use_asset_root else None)

    assert repairs
    assert hits == []
