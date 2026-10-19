import importlib.util

import pytest

from wra.generator.edge_repair import repair_edge_hugging_text


def _playwright_launch_fails() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return True
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return False
    except Exception:
        return True


pytestmark = pytest.mark.skipif(_playwright_launch_fails(), reason="playwright unavailable")

_VIEWPORTS = (390, 768)


def _page(style: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; }}
  {style}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _pattern_a_html() -> str:
    return _page(
        ".container { max-width: 1000px; margin: 0 auto; padding: 0 24px; } #menu { padding: 80px 0; }",
        '<section id="menu" class="container"><h2>Menu heading</h2></section>'
        '<section id="about" class="container"><p>About text</p></section>',
    )


def _pattern_b_html() -> str:
    return _page(
        "* { padding: 0; } .wrap { padding: 0 var(--u) * 4; } :root { --u: 8px; }",
        '<div class="wrap"><h2>Heading</h2></div>',
    )


def _clean_html() -> str:
    return _page(
        "section { padding: 40px 24px; }",
        '<section id="menu"><h2>Menu heading</h2></section>',
    )


def _skipped_html() -> str:
    return _page(
        ".scroller { overflow-x: auto; white-space: nowrap; } .bar { position: fixed; top: 0; left: 0; width: 100%; padding: 0; }",
        '<div class="scroller"><span>Scrolling text</span></div><div class="bar">Fixed bar</div>',
    )


def _text_left(html: str, width: int, selector: str) -> float:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.set_content(html)
            return page.evaluate(
                """(selector) => {
                  const range = document.createRange();
                  range.selectNodeContents(document.querySelector(selector));
                  return range.getClientRects()[0].left;
                }""",
                selector,
            )
        finally:
            browser.close()


def test_pattern_a_takes_padding_from_a_sibling_container():
    html = _pattern_a_html()
    assert all(_text_left(html, width, "#menu h2") < 4 for width in _VIEWPORTS)

    repaired, repairs = repair_edge_hugging_text(html)

    assert repairs == ["section#menu@390,768 24px"]
    assert 'data-wra-repair="edge"' in repaired
    assert all(_text_left(repaired, width, "#menu h2") >= 4 for width in _VIEWPORTS)


def test_pattern_b_falls_back_to_sixteen_pixels():
    html = _pattern_b_html()

    repaired, repairs = repair_edge_hugging_text(html)

    assert repairs and repairs[0].endswith("16px")
    assert all(_text_left(repaired, width, ".wrap h2") >= 4 for width in _VIEWPORTS)


def test_properly_padded_page_is_left_alone():
    html = _clean_html()

    assert repair_edge_hugging_text(html) == (html, [])


def test_scroller_and_fixed_text_are_not_touched():
    html = _skipped_html()

    assert repair_edge_hugging_text(html) == (html, [])


def _computed(html: str, width: int, selector: str, expression: str):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.set_content(html)
            return page.evaluate(f"(selector) => {{ const el = document.querySelector(selector); return {expression}; }}", selector)
        finally:
            browser.close()


def test_only_the_side_without_padding_changes():
    html = _page("section { padding: 0 80px 0 0; }", '<section id="a"><h2>Heading</h2></section>')

    repaired, repairs = repair_edge_hugging_text(html)

    assert repairs == ["section#a@390,768,1280 16px/0px"]
    for width in _VIEWPORTS:
        assert _computed(repaired, width, "#a", "getComputedStyle(el).paddingLeft") == "16px"
        assert _computed(repaired, width, "#a", "getComputedStyle(el).paddingRight") == "80px"


def test_section_with_a_full_width_image_is_padded_and_the_image_shrinks():
    html = _page(
        "img { display: block; width: 100%; height: 40px; background: #ccc; }",
        '<section id="gallery"><img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="><h2>Gallery</h2></section>',
    )

    repaired, repairs = repair_edge_hugging_text(html)

    assert repairs == ["section#gallery@390,768,1280 16px"]
    for width in _VIEWPORTS:
        assert _computed(repaired, width, "img", "el.getBoundingClientRect().width") == width - 32
        assert _text_left(repaired, width, "h2") >= 4
        assert _computed(repaired, width, "html", "el.scrollWidth") == _computed(html, width, "html", "el.scrollWidth")
