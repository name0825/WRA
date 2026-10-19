import importlib.util

import pytest

from wra.generator.skiplink_repair import repair_protruding_skip_link

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")


def _hidden_skip_link_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; line-height: 25.6px; }
  .skip-link { position: absolute; top: -40px; left: 0; padding: 8px; background: #000; color: #fff; }
  .skip-link:focus { top: 0; }
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<main id="main"><p>content</p></main>
</body>
</html>"""


def _shared_class_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; line-height: 25.6px; }
</style>
</head>
<body>
<a class="sr-only" href="#main" style="position:absolute;top:-40px;left:0;padding:8px;">Skip to content</a>
<nav><a class="sr-only" href="#main">Main</a></nav>
<main id="main"><p>content</p></main>
</body>
</html>"""


def _already_hidden_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; line-height: 25.6px; }
  .far { position: absolute; top: -100px; left: 0; padding: 8px; }
  .clipped { position: absolute; top: -40px; left: 0; padding: 8px; clip: rect(0 0 0 0); }
</style>
</head>
<body>
<a class="far" href="#main">Skip far</a>
<a class="clipped" href="#main">Skip clipped</a>
<main id="main"><p>content</p></main>
</body>
</html>"""


def _visible_shifted_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; }
  a.to-top { position: absolute; top: -2px; left: 0; display: block; height: 32px; line-height: 32px; }
</style>
</head>
<body>
<a class="to-top" href="#top">Top</a>
<main id="top"><p>content</p></main>
</body>
</html>"""


def _entry_animation_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; line-height: 25.6px; }
  @keyframes drop { from { transform: translateY(-90%); } to { transform: none; } }
  a.cta { position: fixed; top: 0; left: 0; padding: 8px; animation: drop .3s .5s both; }
</style>
</head>
<body>
<a class="cta" href="#main">Menu</a>
<main id="main"><p>content</p></main>
</body>
</html>"""


def _preceded_skip_link_html(preceding: str) -> str:
    return _hidden_skip_link_html().replace('<a class="skip-link"', f'{preceding}\n<a class="skip-link"')


def _fixed_header_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; line-height: 25.6px; }
  header { position: fixed; top: -60px; left: 0; right: 0; height: 64px; }
  header a { position: absolute; inset: 0; }
</style>
</head>
<body>
<header><a class="logo" href="#main">Brand</a></header>
<main id="main"><p>content</p></main>
</body>
</html>"""


def _transform_skip_link_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; line-height: 25.6px; }
  .skip { position: absolute; top: 0; left: 0; padding: 8px; background: #000; color: #fff; transform: translateY(-95%); }
  .skip:focus { transform: translateY(0); }
</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<main id="main"><p>content</p></main>
</body>
</html>"""


@pytest.fixture(scope="module", autouse=True)
def _require_browser() -> None:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
    except Exception:
        pytest.skip("Playwright launch unavailable in this environment")


def _assert_same_focused_rect(before: dict, after: dict) -> None:
    for key in ("top", "left", "height"):
        assert after[key] == pytest.approx(before[key], abs=0.5)


def _rest_and_focused(html: str, selector: str, width: int = 390, wait: int = 0) -> tuple[dict, dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": width, "height": 800})
            page.set_content(html)
            page.wait_for_timeout(wait)
            rest = page.eval_on_selector(selector, "el => el.getBoundingClientRect().toJSON()")
            page.eval_on_selector(selector, "el => el.focus()")
            focused = page.eval_on_selector(selector, "el => el.getBoundingClientRect().toJSON()")
            return rest, focused
        finally:
            browser.close()


def test_hides_protruding_skip_link_at_rest_and_keeps_focus_reveal() -> None:
    html = _hidden_skip_link_html()
    before_rest, before_focused = _rest_and_focused(html, "a.skip-link")
    assert before_rest["bottom"] > 0

    repaired, repairs = repair_protruding_skip_link(html)

    assert repairs == ["a.skip-link +1.6px"]
    assert 'data-wra-repair="skip-link"' in repaired
    for width in (390, 768, 1440):
        rest, focused = _rest_and_focused(repaired, "a.skip-link", width)
        assert rest["bottom"] <= 0
        _assert_same_focused_rect(before_focused, focused)
    assert before_focused["top"] == 0


def test_repairs_inline_styled_link_through_a_unique_selector() -> None:
    html = _shared_class_html()

    repaired, repairs = repair_protruding_skip_link(html)

    assert len(repairs) == 1
    assert repairs[0].startswith("body > a.sr-only")
    rest, _ = _rest_and_focused(repaired, "body > a.sr-only")
    assert rest["bottom"] <= 0
    nav_rest, _ = _rest_and_focused(repaired, "nav a.sr-only")
    assert nav_rest["top"] >= 0


def test_leaves_already_hidden_links_unchanged() -> None:
    html = _already_hidden_html()

    repaired, repairs = repair_protruding_skip_link(html)

    assert repaired == html
    assert repairs == []


def test_leaves_visible_shifted_anchor_unchanged() -> None:
    html = _visible_shifted_html()

    repaired, repairs = repair_protruding_skip_link(html)

    assert repaired == html
    assert repairs == []


def test_repair_is_idempotent() -> None:
    html = _hidden_skip_link_html()
    repaired, repairs = repair_protruding_skip_link(html)
    assert repairs

    again, again_repairs = repair_protruding_skip_link(repaired)

    assert again == repaired
    assert again_repairs == []


def test_leaves_link_revealed_by_entry_animation_unchanged() -> None:
    html = _entry_animation_html()

    repaired, repairs = repair_protruding_skip_link(html)

    assert repaired == html
    assert repairs == []
    rest, _ = _rest_and_focused(repaired, "a.cta", wait=1200)
    assert rest["top"] == pytest.approx(0, abs=0.5)
    assert rest["bottom"] == pytest.approx(41.6, abs=0.5)


@pytest.mark.parametrize("preceding", ['<button type="button">Menu</button>', '<a href="/home">Home</a>'])
def test_leaves_link_that_is_not_the_first_focusable_element_unchanged(preceding: str) -> None:
    html = _preceded_skip_link_html(preceding)
    before_rest, _ = _rest_and_focused(html, "a.skip-link")
    assert before_rest["bottom"] == pytest.approx(1.6, abs=0.1)

    repaired, repairs = repair_protruding_skip_link(html)

    assert repaired == html
    assert repairs == []


def test_leaves_link_inside_offscreen_fixed_header_unchanged() -> None:
    html = _fixed_header_html()
    before_rest, _ = _rest_and_focused(html, "a.logo")
    assert before_rest["bottom"] == pytest.approx(4, abs=0.1)

    repaired, repairs = repair_protruding_skip_link(html)

    assert repaired == html
    assert repairs == []


def test_drops_stale_block_when_link_becomes_visible() -> None:
    original = _hidden_skip_link_html()
    repaired, _ = repair_protruding_skip_link(original)
    assert 'data-wra-repair="skip-link"' in repaired
    visible = original.replace("position: absolute; top: -40px; left: 0;", "display: block;")
    assert visible != original
    stale = repaired.replace("position: absolute; top: -40px; left: 0;", "display: block;")

    again, again_repairs = repair_protruding_skip_link(stale)

    assert again == visible
    assert again_repairs == []
    assert 'data-wra-repair="skip-link"' not in again
    expected_rest, _ = _rest_and_focused(visible, "a.skip-link")
    rest, _ = _rest_and_focused(again, "a.skip-link")
    assert rest["top"] == pytest.approx(expected_rest["top"], abs=0.5)
    assert rest["top"] == pytest.approx(0, abs=0.5)


def test_replaces_stale_block_when_link_class_is_renamed() -> None:
    repaired, _ = repair_protruding_skip_link(_hidden_skip_link_html())
    renamed = (
        repaired.replace('class="skip-link"', 'class="skip"')
        .replace(".skip-link {", ".skip {")
        .replace(".skip-link:focus {", ".skip:focus {")
    )
    assert renamed.count("skip-link") == 2

    again, again_repairs = repair_protruding_skip_link(renamed)

    assert again_repairs == ["a.skip +1.6px"]
    assert again.count('data-wra-repair="skip-link"') == 1
    assert "a.skip:not(:focus):not(:focus-within)" in again
    assert "a.skip-link" not in again
    rest, _ = _rest_and_focused(again, "a.skip")
    assert rest["bottom"] <= 0


def test_hides_transform_revealed_skip_link_and_keeps_focused_rect() -> None:
    html = _transform_skip_link_html()
    before_rest, before_focused = _rest_and_focused(html, "a.skip")
    assert before_rest["bottom"] == pytest.approx(2.1, abs=0.1)

    repaired, repairs = repair_protruding_skip_link(html)

    assert repairs == ["a.skip +2.1px"]
    for width in (390, 768, 1440):
        rest, focused = _rest_and_focused(repaired, "a.skip", width)
        assert rest["bottom"] <= 0
        _assert_same_focused_rect(before_focused, focused)


@pytest.mark.parametrize("placement", ["body_style", "contrast_block"])
def test_reindented_block_does_not_swallow_following_content(placement: str) -> None:
    repaired, _ = repair_protruding_skip_link(_hidden_skip_link_html())
    rule = "a.skip-link:not(:focus):not(:focus-within) { transform: translateY(-100%) !important; }"
    assert f"{rule}\n</style>" in repaired
    contrast = '<style data-wra-repair="contrast">\n.x { color: #000; }\n</style>'
    late = '<style>\n.late { color: red; }\n</style>\n<p class="late">late</p>'
    html = repaired.replace(f"{rule}\n</style>", f"{rule}\n  </style>")
    if placement == "contrast_block":
        html = html.replace("  </style>\n", f"  </style>\n{contrast}\n", 1)
    else:
        html = html.replace("</main>", f"</main>\n{late}", 1)

    again, _ = repair_protruding_skip_link(html)

    assert again.count('data-wra-repair="skip-link"') == 1
    assert "</head>" in again and "<body>" in again
    assert 'href="#main"' in again
    if placement == "contrast_block":
        assert contrast in again
    else:
        assert late in again
    rest, _ = _rest_and_focused(again, "a.skip-link")
    assert rest["bottom"] <= 0


def test_repairs_skip_link_on_page_with_opacity_fade_in() -> None:
    html = _hidden_skip_link_html().replace(
        "body { margin: 0; line-height: 25.6px; }",
        "body { margin: 0; line-height: 25.6px; animation: fade .4s ease forwards; }\n"
        "  @keyframes fade { from { opacity: 0; } to { opacity: 1; } }",
    )

    repaired, repairs = repair_protruding_skip_link(html)

    assert repairs == ["a.skip-link +1.6px"]
    rest, _ = _rest_and_focused(repaired, "a.skip-link", wait=1000)
    assert rest["bottom"] <= 0


@pytest.mark.parametrize(
    "build",
    [
        lambda: _preceded_skip_link_html('<div tabindex="-1"></div>'),
        lambda: _hidden_skip_link_html().replace("<body>", '<body>\n<div tabindex="-1">').replace("</body>", "</div>\n</body>"),
        lambda: _preceded_skip_link_html('<a href="/menu" style="display:none">Menu</a>'),
        lambda: _preceded_skip_link_html('<input type="hidden" name="token">'),
        lambda: _preceded_skip_link_html("<button disabled>Menu</button>"),
    ],
    ids=["tabindex_div", "tabindex_wrapper", "display_none_link", "hidden_input", "disabled_button"],
)
def test_ignores_unfocusable_elements_before_the_skip_link(build) -> None:
    repaired, repairs = repair_protruding_skip_link(build())

    assert repairs == ["a.skip-link +1.6px"]
    rest, _ = _rest_and_focused(repaired, "a.skip-link")
    assert rest["bottom"] <= 0
