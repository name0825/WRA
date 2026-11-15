import importlib.util

import pytest

from wra.generator.hangul_wrap import repair_hangul_heading_wrap


def _page(body: str, style: str = "") -> str:
    return f'<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>{style}</style></head><body>{body}</body></html>'


def test_injected_for_hangul_heading():
    html = _page("<h2>파트너 소개</h2><p>text</p>")
    out, repairs = repair_hangul_heading_wrap(html)
    assert repairs
    assert out.count('data-wra-repair="hangul-wrap"') == 1
    assert "word-break:keep-all" in out
    assert out.index("hangul-wrap") < out.index("</head>")


def test_injected_when_hangul_is_inside_nested_tag_and_heading_has_attrs():
    out, repairs = repair_hangul_heading_wrap(_page('<h1 class="t">Hello <span>안녕하세요</span></h1>'))
    assert repairs and "hangul-wrap" in out


def test_not_injected_for_non_hangul_headings():
    html = _page("<h1>Partners</h1><h2>About us</h2>")
    out, repairs = repair_hangul_heading_wrap(html)
    assert out == html and repairs == []


def test_not_injected_when_hangul_only_in_body_text():
    html = _page("<h1>Partners</h1><p>파트너 소개</p>")
    out, repairs = repair_hangul_heading_wrap(html)
    assert out == html and repairs == []


def test_not_injected_when_hangul_only_in_heading_attribute():
    html = _page('<h1 title="파트너">Partners</h1>')
    out, repairs = repair_hangul_heading_wrap(html)
    assert out == html and repairs == []


def test_idempotent():
    once, _ = repair_hangul_heading_wrap(_page("<h2>파트너 소개</h2>"))
    twice, repairs = repair_hangul_heading_wrap(once)
    assert twice == once and repairs == []


def test_style_inserted_after_doctype_when_no_head_or_body():
    out, repairs = repair_hangul_heading_wrap("<!DOCTYPE html><h2>파트너 소개</h2>")
    assert repairs
    assert out.startswith("<!DOCTYPE html>")
    assert "word-break:keep-all" in out


def test_rule_uses_where_selector():
    out, _ = repair_hangul_heading_wrap(_page("<h2>파트너 소개</h2>", "h2{word-break:normal}"))
    assert ":where(h1,h2,h3,h4,h5,h6){" in out


@pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")
def test_author_word_break_wins_and_keep_all_applies():
    from playwright.sync_api import sync_playwright

    html = _page('<h2 id="a">파트너 소개</h2><h2 id="b" class="x">파트너 소개</h2>', ".x{word-break:break-all}")
    out, _ = repair_hangul_heading_wrap(html)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(out)
            values = page.evaluate("['a','b'].map(i => getComputedStyle(document.getElementById(i)).wordBreak)")
        finally:
            browser.close()
    assert values == ["keep-all", "break-all"]
