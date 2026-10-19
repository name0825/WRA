import importlib.util

import pytest

from wra.generator.squeeze_repair import repair_squeezed_text

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")


def _squeezed_strip_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; }
  .hero__strip { display: flex; flex-wrap: nowrap; justify-content: space-between; align-items: center; gap: 16px; padding: 12px; }
  .hero__badge { white-space: nowrap; background: #eee; padding: 8px 16px; }
  h1 { margin: 0; font-size: 40px; }
</style>
</head>
<body>
<div class="hero__strip">
  <h1>행복빵집</h1>
  <span class="hero__badge">한정판매 특별 할인 이벤트 진행중</span>
</div>
<main><p>content</p></main>
</body>
</html>"""


def _no_issue_html() -> str:
    return """<!DOCTYPE html>
<html>
<head><style>body { margin: 0; } h1 { font-size: 24px; }</style></head>
<body>
<div class="hero__strip"><h1>Bakery</h1></div>
<main><p>content</p></main>
</body>
</html>"""


def _non_flex_char_wrap_html() -> str:
    return """<!DOCTYPE html>
<html>
<head><style>body { margin: 0; } .narrow { width: 20px; }</style></head>
<body>
<div class="narrow"><p>가나다라마바사아자차카타파하</p></div>
<main><p>content</p></main>
</body>
</html>"""


def _skip_if_no_browser(repaired: str, original: str) -> None:
    if repaired == original:
        pytest.skip("Playwright launch unavailable in this environment")


def test_repairs_squeezed_heading_and_stops_char_wrap() -> None:
    html = _squeezed_strip_html()

    repaired, repairs = repair_squeezed_text(html)

    _skip_if_no_browser(repaired, html)
    assert repairs
    assert any(entry.startswith("flexwrap: div.hero__strip") for entry in repairs)
    assert 'data-wra-repair="squeeze"' in repaired
    assert "flex-wrap: wrap" in repaired

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            for width in (390, 768):
                page.set_viewport_size({"width": width, "height": 800})
                page.set_content(repaired)
                rects = page.eval_on_selector(
                    "h1",
                    "el => { const r = document.createRange(); r.selectNodeContents(el); "
                    "return Array.from(r.getClientRects()).filter(x => x.width > 0 && x.height > 0).length; }",
                )
                assert rects <= 2
                scroll_width = page.evaluate("() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
                assert scroll_width <= width
        finally:
            browser.close()


def test_repairs_squeezed_heading_with_utility_class_names() -> None:
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)
    html = probe_html.replace("<h1>", '<h1 class="md:text-5xl bg-white/95">')

    repaired, repairs = repair_squeezed_text(html)

    assert any(entry.startswith("flexwrap: div.hero__strip") for entry in repairs)
    assert 'data-wra-repair="squeeze"' in repaired


def test_repairs_squeezed_nav_links_without_unique_selector() -> None:
    html = """<!DOCTYPE html>
<html>
<head><style>
body { margin: 0; font-size: 16px; }
.header-inner { display: flex; flex-wrap: nowrap; align-items: center; height: 80px; }
.logo { display: block; width: 300px; height: 20px; flex-shrink: 0; }
.nav-links { display: flex; flex-wrap: nowrap; gap: 2rem; list-style: none; margin: 0; padding: 0; }
a { color: #000; }
</style></head>
<body>
<div class="header-inner">
<span class="logo"></span>
<nav><ul class="nav-links"><li><a href="#a">신뢰도</a></li><li><a href="#b">연락처</a></li><li><a href="#c">서비스</a></li><li><a href="#d">문의하기</a></li><li><a href="#e">자주묻는</a></li><li><a href="#f">이용안내</a></li></ul></nav>
</div>
</body>
</html>"""

    repaired, repairs = repair_squeezed_text(html)

    _skip_if_no_browser(repaired, html)
    assert repairs
    assert any(entry.startswith("flexwrap:") for entry in repairs)
    assert any(entry.startswith("keepall:") for entry in repairs)
    assert "word-break: keep-all" in repaired
    assert any(entry.startswith("unclip:") for entry in repairs)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 390, "height": 800})
            page.set_content(repaired)
            tops = page.eval_on_selector_all(".nav-links a", "els => els.map(e => e.getBoundingClientRect().top)")
            assert tops and min(tops) >= 0
        finally:
            browser.close()


def _nav_html(extra_css: str, before: str, wrapper_open: str, wrapper_close: str, links: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><style>
body {{ margin: 0; font-size: 16px; }}
.header-inner {{ display: flex; flex-wrap: nowrap; align-items: center; }}
.logo {{ display: block; width: 300px; height: 20px; flex-shrink: 0; }}
.nav-links {{ display: flex; flex-wrap: nowrap; gap: 2rem; list-style: none; margin: 0; padding: 0; }}
{extra_css}
</style></head>
<body>
{before}{wrapper_open}
<div class="header-inner">
<span class="logo"></span>
<nav><ul class="nav-links">{links}</ul></nav>
</div>
{wrapper_close}
</body>
</html>"""


_NAV_LINKS = "".join(f'<li><a href="#{i}">{text}</a></li>' for i, text in enumerate(("신뢰도", "연락처", "서비스")))


def test_repairs_squeezed_row_below_fold_with_overflow_x_hidden_root() -> None:
    html = _nav_html(
        "html, body { overflow-x: hidden; }", '<div style="height: 1200px"></div>', "", "", _NAV_LINKS
    )

    repaired, repairs = repair_squeezed_text(html)

    _skip_if_no_browser(repaired, html)
    assert any(entry.startswith("flexwrap:") for entry in repairs)
    assert "word-break: keep-all" in repaired


def test_rejects_repair_when_unrelaxable_ancestor_clips_wrapped_row() -> None:
    html = _nav_html(
        ".clip { position: absolute; top: 0; left: 0; width: 100%; height: 70px; overflow: hidden; }",
        "",
        '<div class="clip">',
        "</div>",
        _NAV_LINKS,
    )

    repaired, repairs = repair_squeezed_text(html)

    assert repaired == html
    assert repairs == []


def test_leaves_html_unchanged_when_no_issue() -> None:
    html = _no_issue_html()

    repaired, repairs = repair_squeezed_text(html)

    assert repaired == html
    assert repairs == []


def test_leaves_char_wrap_unchanged_when_not_in_flex_row() -> None:
    html = _non_flex_char_wrap_html()

    repaired, repairs = repair_squeezed_text(html)

    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright not installed")
    assert repaired == html
    assert repairs == []


def test_char_wrap_ignores_text_wrapped_only_at_word_boundaries() -> None:
    from playwright.sync_api import sync_playwright

    from wra.browser.evaluator import CHAR_WRAP_SCRIPT

    html = """<!DOCTYPE html>
<html>
<head><style>body { margin: 0; font-size: 16px; } td { width: 60px; } .narrow { width: 20px; }</style></head>
<body>
<a href="#main" style="position: absolute; left: -9999px; top: auto; width: 1px; height: 1px; overflow: hidden;">메인 콘텐츠로 이동</a>
<a href="#main" style="position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0);">본문 바로가기 링크</a>
<table><tr><td>Dr. Taemin Oh</td><td>Junho Bae</td></tr></table>
<div class="narrow"><p>가나다라마바사아자차카타파하</p></div>
</body>
</html>"""

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_viewport_size({"width": 390, "height": 800})
                page.set_content(html)
                flagged = [item["text"] for item in page.evaluate(CHAR_WRAP_SCRIPT)]
            finally:
                browser.close()
    except Exception:
        pytest.skip("Playwright launch unavailable in this environment")

    assert flagged == ["가나다라마바사아자차카타파하"]


def _squeezed_sibling_list_html() -> str:
    items = "".join(
        '<li><strong>가나다라마바사</strong><span class="note">한정판매 특별 할인 이벤트 진행중 안내 문구입니다</span></li>'
        for _ in range(4)
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; }}
  ul {{ list-style: none; margin: 0; padding: 0; width: 300px; }}
  li {{ display: flex; flex-wrap: nowrap; gap: 8px; }}
  .note {{ white-space: nowrap; }}
  strong {{ font-size: 24px; }}
</style>
</head>
<body>
<ul class="product-list">{items}</ul>
<main><p>content</p></main>
</body>
</html>"""


def test_repairs_squeezed_item_among_identical_siblings() -> None:
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)

    repaired, repairs = repair_squeezed_text(_squeezed_sibling_list_html())

    assert any(entry.startswith("flexwrap: li:nth-of-type(") for entry in repairs)


def test_unclips_fixed_height_ancestor_among_identical_siblings() -> None:
    card = (
        '<div class="card"><div class="hero__strip"><h1>행복빵집</h1>'
        '<span class="hero__badge">한정판매 특별 할인 이벤트 진행중</span></div></div>'
    )
    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; }}
  .card {{ height: 70px; overflow: hidden; }}
  .hero__strip {{ display: flex; flex-wrap: nowrap; justify-content: space-between; align-items: center; gap: 16px; padding: 12px; }}
  .hero__badge {{ white-space: nowrap; background: #eee; padding: 8px 16px; }}
  h1 {{ margin: 0; font-size: 40px; }}
</style>
</head>
<body>
{card * 3}
<main><p>content</p></main>
</body>
</html>"""
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)

    repaired, repairs = repair_squeezed_text(html)

    assert any(entry.startswith("unclip: div.card:nth-of-type(") for entry in repairs)


def _twelve_column_grid_html(item_count: int) -> str:
    filler = "".join(f"<div><p>항목 {i}</p></div>" for i in range(item_count - 1))
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; }}
  .split {{ display: grid; gap: 16px; }}
  @media (min-width: 1024px) {{ .split {{ grid-template-columns: repeat(12, 1fr); }} }}
  h1 {{ margin: 0; font-size: 40px; }}
</style>
</head>
<body>
<div class="split">
  <div><h1>행복빵집이 만드는 하루의 시작</h1></div>
  {filler}
</div>
<main><p>content</p></main>
</body>
</html>"""


def _measure_heading(repaired: str, widths: tuple[int, ...]) -> dict[int, tuple[int, int, int]]:
    from playwright.sync_api import sync_playwright

    measured: dict[int, tuple[int, int, int]] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            for width in widths:
                page.set_viewport_size({"width": width, "height": 800})
                page.set_content(repaired)
                lines = page.eval_on_selector(
                    "h1",
                    "el => { const r = document.createRange(); r.selectNodeContents(el); "
                    "return Array.from(r.getClientRects()).filter(x => x.width > 0 && x.height > 0).length; }",
                )
                scroll_width = page.evaluate("() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
                columns = page.evaluate("() => getComputedStyle(document.querySelector('.split')).gridTemplateColumns.split(' ').length")
                measured[width] = (lines, scroll_width, columns)
        finally:
            browser.close()
    return measured


def test_repairs_heading_squeezed_into_one_track_of_a_twelve_column_grid() -> None:
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)
    html = _twelve_column_grid_html(2)
    before = _measure_heading(html, (390, 768, 800, 1000, 1280))

    repaired, repairs = repair_squeezed_text(html)

    assert before[1280][0] >= 6
    assert any(entry.startswith("gridcols: div.split") and entry.endswith("@1280px") for entry in repairs)
    assert "repeat(2, minmax(0, 1fr))" in repaired
    after = _measure_heading(repaired, (390, 768, 800, 1000, 1280))
    assert after[1280][0] <= 2
    for width in (390, 768, 800, 1000):
        assert after[width] == before[width]
    assert after[1280][1] <= 1280


def test_falls_back_to_full_row_span_when_every_track_holds_an_item() -> None:
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)
    html = _twelve_column_grid_html(12)

    repaired, repairs = repair_squeezed_text(html)

    assert any(entry.startswith("gridspan: div.split") for entry in repairs)
    assert not any(entry.startswith("gridcols:") for entry in repairs)
    assert "grid-column: 1 / -1" in repaired
    after = _measure_heading(repaired, (390, 768, 1280))
    assert after[1280][0] <= 2
    assert after[1280][1] <= 1280


def _padded_aside_grid_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; }
  .g { display: grid; gap: 16px; }
  @media (min-width: 1024px) {
    .g { grid-template-columns: repeat(12, 1fr); }
    .main { grid-column: span 8; }
    .aside { grid-column: span 4; padding: 0 170px; }
  }
  h2 { margin: 0; font-size: 40px; }
</style>
</head>
<body>
<div class="g">
  <div class="main"><p>content</p></div>
  <div class="aside"><h2>행복빵집이 만드는 하루의 시작</h2></div>
</div>
</body>
</html>"""


def _column_widths(html: str, width: int) -> list[int]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": width, "height": 800})
            page.set_content(html)
            return page.evaluate("() => ['.main', '.aside'].map(s => Math.round(document.querySelector(s).getBoundingClientRect().width))")
        finally:
            browser.close()


def test_leaves_spanning_grid_item_squeezed_by_its_own_padding_unchanged() -> None:
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)
    html = _padded_aside_grid_html()

    repaired, repairs = repair_squeezed_text(html)

    assert not any(entry.startswith(("gridcols:", "gridspan:")) for entry in repairs)
    assert _column_widths(repaired, 1280) == _column_widths(html, 1280)


def test_flex_repairs_survive_an_unrepairable_grid_candidate() -> None:
    plain = _squeezed_strip_html()
    _, expected = repair_squeezed_text(plain)
    if not expected:
        pytest.skip("Playwright launch unavailable in this environment")
    grid = (
        '<div style="display:grid;grid-template-columns:1fr 1fr"><div>'
        '<p style="width:20px;font-size:30px">가나다라마바사아자차</p></div><div>x</div></div>'
    )
    html = plain.replace("<main>", grid + "<main>")

    repaired, repairs = repair_squeezed_text(html)

    assert repairs == expected
    assert "flex-wrap: wrap" in repaired
    assert "grid-column" not in repaired


def _cards_grid_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; }
  .g { display: grid; gap: 16px; }
  @media (min-width: 1024px) {
    .g { grid-template-columns: repeat(12, 1fr); }
    .card { grid-column: span 4; }
  }
  h1 { margin: 0; font-size: 40px; }
</style>
</head>
<body>
<div class="g">
  <div><h1>행복빵집이 만드는 하루의 시작</h1></div>
  <div class="card"><p>하나</p></div>
  <div class="card"><p>둘</p></div>
  <div class="card"><p>셋</p></div>
</div>
</body>
</html>"""


def test_full_row_span_targets_only_the_squeezed_item_when_siblings_have_spans() -> None:
    probe_html = _squeezed_strip_html()
    probe, _ = repair_squeezed_text(probe_html)
    _skip_if_no_browser(probe, probe_html)
    html = _cards_grid_html()

    repaired, repairs = repair_squeezed_text(html)

    assert any(entry.startswith("gridspan:") and entry.endswith("@1280px") for entry in repairs)
    assert not any(entry.startswith("gridcols:") for entry in repairs)
    assert "> *" not in repaired
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            widths = []
            for content in (html, repaired):
                page.set_content(content)
                widths.append(page.evaluate("() => Array.from(document.querySelectorAll('.card')).map(e => Math.round(e.getBoundingClientRect().width))"))
            lines = page.eval_on_selector(
                "h1",
                "el => { const r = document.createRange(); r.selectNodeContents(el); "
                "return Array.from(r.getClientRects()).filter(x => x.width > 0 && x.height > 0).length; }",
            )
        finally:
            browser.close()
    assert widths[0] == widths[1] == [416, 416, 416]
    assert lines <= 2
