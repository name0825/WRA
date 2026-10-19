import importlib.util

import pytest

from wra.generator.overflow_repair import _verified, repair_horizontal_overflow

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


def _gutter_html() -> str:
    return _page(
        ".row { display: flex; margin-left: -24px; margin-right: -24px; } .row > div { flex: 1; }",
        '<section class="features"><div class="row"><div>a</div><div>b</div></div></section>',
    )


def _offcanvas_html(top: str) -> str:
    return _page(
        f"header.site-header {{ position: relative; height: 60px; }} "
        f"ul#nav-menu {{ position: absolute; top: {top}; left: 100%; width: 300px; height: 400px; margin: 0; }}",
        '<header class="site-header"><ul id="nav-menu"><li>Home</li></ul></header><main><p>content</p></main>',
    )


def _reveal_html() -> str:
    return _page(
        "section#features { padding: 20px; } .tile { width: 200px; opacity: 0; transform: translateX(300px); }",
        '<section id="features"><div class="tile">one</div></section>',
    )


def _navwrap_html() -> str:
    return _page(
        "ul#nav-menu { position: static; display: flex; flex-wrap: nowrap; flex-direction: row; margin: 0; padding: 0; list-style: none; } "
        "ul#nav-menu li { flex: none; width: 100px; }",
        '<header><ul id="nav-menu"><li>a</li><li>b</li><li>c</li><li>d</li><li>e</li></ul></header>',
    )


def _gridcollapse_html() -> str:
    return _page(
        ".feature-matrix { display: grid; grid-template-columns: repeat(3, 1fr); } .feature-tile { min-width: 200px; height: 40px; }",
        '<div class="page"><div class="feature-matrix"><div class="feature-tile">a</div><div class="feature-tile">b</div><div class="feature-tile">c</div></div></div>',
    )


def _scroll_widths(html: str, viewports: tuple[int, ...] = (390, 768, 1280)) -> dict[int, tuple[int, int]]:
    from playwright.sync_api import sync_playwright

    widths = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            for width in viewports:
                page.set_viewport_size({"width": width, "height": 800})
                page.set_content(html)
                widths[width] = tuple(page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]"))
        finally:
            browser.close()
    return widths


def _assert_repaired(html: str, expected_rule: str, expected_type: str) -> str:
    assert any(scroll > viewport for scroll, viewport in _scroll_widths(html).values())

    repaired, repairs = repair_horizontal_overflow(html)

    assert repaired != html
    fixes = [r for r in repairs if not r.startswith("scrollcue: ")]
    assert fixes and all(r.startswith(f"{expected_type}: ") for r in fixes)
    assert 'data-wra-repair="overflow"' in repaired
    assert expected_rule in repaired
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport
    return repaired


def test_repairs_uncompensated_row_gutter() -> None:
    _assert_repaired(_gutter_html(), "section.features { padding-left: 24px !important; padding-right: 24px !important; }", "gutter")


def test_repairs_same_labeled_gutter_rows_in_separate_containers() -> None:
    row = '<div class="container"><div class="row"><div>a</div><div>b</div></div></div>'
    html = _page(
        ".row { display: flex; margin-left: -8px; margin-right: -8px; } .row > div { flex: 1; }",
        f'<section id="intro">{row}</section><section id="about">{row}</section>',
    )

    repaired = _assert_repaired(html, "section#intro > div.container", "gutter")

    assert "section#about > div.container" in repaired


def test_scopes_gutter_repair_to_widths_that_overflowed() -> None:
    html = _page(
        ".row { display: flex; margin-left: -24px; margin-right: -24px; } .row > div { flex: 1; } "
        "section.features { padding: 0 12px; } "
        "@media (min-width: 768px) { section.features { max-width: 600px; margin: 0 auto; } }",
        '<section class="features"><div class="row"><div>a</div><div>b</div></div></section>',
    )
    repaired = _assert_repaired(
        html,
        "@media (max-width: 767px) { section.features { padding-left: 24px !important; padding-right: 24px !important; } }",
        "gutter",
    )

    from playwright.sync_api import sync_playwright

    widths = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            for content in (html, repaired):
                page.set_content(content)
                widths.append(page.evaluate("() => [document.querySelector('.row').getBoundingClientRect().width, getComputedStyle(document.querySelector('section.features')).paddingLeft]"))
        finally:
            browser.close()
    assert widths[0] == widths[1]


def test_repairs_absolute_offcanvas_menu_by_making_it_fixed() -> None:
    _assert_repaired(_offcanvas_html("0"), "ul#nav-menu { position: fixed !important; }", "offcanvas")


def test_repairs_offcanvas_menu_by_clipping_header_when_fixed_would_move_it() -> None:
    repaired = _assert_repaired(_offcanvas_html("100%"), "header.site-header { overflow-x: clip !important; }", "offcanvas")
    assert "position: fixed" not in repaired


def test_repairs_unrevealed_translated_element() -> None:
    _assert_repaired(_reveal_html(), "section#features { overflow-x: clip !important; }", "reveal")


def _assert_scoped_and_rect_preserved(html: str, target_selector: str, expected_rule: str, expected_type: str) -> None:
    repaired = _assert_repaired(html, f"@media (max-width: 767px) {{ {expected_rule} }}", expected_type)

    from playwright.sync_api import sync_playwright

    rects = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            for content in (html, repaired):
                page.set_content(content)
                rects.append(page.evaluate(f"() => {{ const r = document.querySelector({target_selector!r}).getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }}"))
        finally:
            browser.close()
    assert rects[0] == rects[1]


def test_repairs_nowrap_flex_nav_by_wrapping() -> None:
    _assert_scoped_and_rect_preserved(
        _navwrap_html(), "ul#nav-menu", "ul#nav-menu { flex-wrap: wrap !important; max-width: 100% !important; }", "navwrap"
    )


def _navwrap_descendant_html() -> str:
    return _page(
        ".nav-container { display: flex; flex-wrap: nowrap; align-items: center; justify-content: space-between; padding: 0 16px; } "
        ".logo { flex: none; width: 60px; } "
        "nav { display: block; } "
        "nav ul { display: flex; flex-wrap: nowrap; gap: 24px; list-style: none; margin: 0; } "
        "nav a { display: block; width: 60px; }",
        '<header><div class="container nav-container"><div class="logo">L</div>'
        '<nav><ul><li><a href="#">a</a></li><li><a href="#">b</a></li><li><a href="#">c</a></li>'
        '<li><a href="#">d</a></li><li><a href="#">e</a></li></ul></nav></div></header>',
    )


def test_repairs_nowrap_flex_row_inside_block_nav_offender() -> None:
    _assert_scoped_and_rect_preserved(
        _navwrap_descendant_html(), "nav ul", "ul { flex-wrap: wrap !important; max-width: 100% !important; }", "navwrap"
    )


def test_navwrap_ignores_scrolling_carousel_inside_offender() -> None:
    html = _page(
        ".nav-container { display: flex; flex-wrap: nowrap; padding: 0 16px; } "
        ".logo { flex: none; width: 60px; } "
        ".group { display: block; } "
        ".carousel { display: flex; flex-wrap: nowrap; overflow-x: auto; width: 200px; } "
        ".carousel span { flex: none; width: 100px; } "
        "nav ul { display: flex; flex-wrap: nowrap; gap: 24px; list-style: none; margin: 0; } "
        "nav a { display: block; width: 60px; }",
        '<header><div class="container nav-container"><div class="logo">L</div><div class="group">'
        '<div class="carousel"><span>1</span><span>2</span><span>3</span><span>4</span></div>'
        '<nav><ul><li><a href="#">a</a></li><li><a href="#">b</a></li><li><a href="#">c</a></li>'
        '<li><a href="#">d</a></li><li><a href="#">e</a></li></ul></nav></div></div></header>',
    )
    _assert_scoped_and_rect_preserved(html, "nav ul", "ul { flex-wrap: wrap !important; max-width: 100% !important; }", "navwrap")


def test_navwrap_falls_back_to_ancestor_when_inner_row_wrap_does_not_help() -> None:
    html = _page(
        ".nav-container { display: flex; flex-wrap: nowrap; } "
        ".logo { flex: none; width: 100px; } "
        ".group { display: block; flex: none; width: 320px; } "
        "ul.links { display: flex; flex-wrap: nowrap; list-style: none; margin: 0; padding: 0; } "
        "ul.links li { flex: none; width: 60px; }",
        '<header><div class="nav-container"><div class="logo">L</div><div class="group">'
        '<ul class="links"><li>a</li><li>b</li><li>c</li><li>d</li><li>e</li></ul></div></div></header>',
    )
    repaired = _assert_repaired(
        html, "@media (max-width: 767px) { div.nav-container { flex-wrap: wrap !important; max-width: 100% !important; } }", "navwrap"
    )
    assert "ul.links { flex-wrap" not in repaired


def test_repairs_grid_that_does_not_collapse_columns() -> None:
    _assert_scoped_and_rect_preserved(
        _gridcollapse_html(), ".feature-matrix", "div.feature-matrix { grid-template-columns: minmax(0,1fr) !important; }", "gridcollapse"
    )


def test_repairs_grid_overflowing_viewport_by_less_than_one_pixel() -> None:
    html = _page(
        ".feature-matrix { display: grid; grid-template-columns: repeat(3, 1fr); } .feature-tile { min-width: 256.3px; height: 40px; }",
        '<div class="page"><div class="feature-matrix"><div class="feature-tile">a</div><div class="feature-tile">b</div><div class="feature-tile">c</div></div></div>',
    )
    assert _scroll_widths(html)[768] == (769, 768)

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    assert any(r.endswith("@768px") for r in repairs)
    assert _scroll_widths(repaired)[768] == (768, 768)


def test_repairs_grid_beneath_wrapper_overflowing_viewport_by_less_than_one_pixel() -> None:
    html = _page(
        ".wrap { width: calc(100% + 0.6px); } .g { display: grid; grid-template-columns: repeat(3, 300px); } .t { height: 40px; }",
        '<div class="wrap"><div class="g"><div class="t">a</div><div class="t">b</div><div class="t">c</div></div></div>',
    )
    assert _scroll_widths(html)[768][0] == 900

    repaired, repairs = repair_horizontal_overflow(html)

    assert any(r.startswith("gridcollapse: ") and r.endswith("@768px") for r in repairs)
    assert _scroll_widths(repaired)[390] == (391, 390)
    assert _scroll_widths(repaired)[768] == (769, 768)


_ASYM_STYLE = (
    ".asym { display: grid; grid-template-columns: 1fr; gap: 32px; } "
    "@media (min-width: 1024px) { .asym { grid-template-columns: 35% 65%; } } "
    ".t { height: 40px; } .fg { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }"
)


def _grid_columns(html: str, selector: str, width: int) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": width, "height": 800})
            page.set_content(html)
            return page.evaluate(
                f"() => getComputedStyle(document.querySelector({selector!r})).gridTemplateColumns.split(' ').length"
            )
        finally:
            browser.close()


def test_gap_only_grid_overflow_keeps_its_columns() -> None:
    html = _page(_ASYM_STYLE, '<div class="page"><div class="asym"><div class="t">a</div><div class="t">b</div></div></div>')
    assert _scroll_widths(html)[1280] == (1312, 1280)

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    assert "@media (min-width: 1280px) { div.asym { grid-template-columns: minmax(0, " in repaired
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport
    assert _grid_columns(repaired, ".asym", 1280) == 2
    assert _grid_columns(repaired, ".asym", 768) == 1
    assert _grid_columns(repaired, ".asym", 390) == 1


def test_repairs_grid_whose_overflowing_children_are_identical_nested_grids() -> None:
    cell = '<div class="fg"><div class="t">a</div><div class="t">b</div></div>'
    html = _page(_ASYM_STYLE, f'<div class="page"><div class="asym">{cell * 4}</div></div>')
    assert _scroll_widths(html)[1280][0] > 1280

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport


def test_candidate_already_fixed_by_an_accepted_ancestor_repair_gets_no_repair_of_its_own() -> None:
    nested = '<div id="{0}" class="fg"><div class="t">a</div><div class="t">b</div></div>'
    html = _page(
        _ASYM_STYLE,
        f'<div class="page"><div class="asym"><div class="t">l</div>{nested.format("x")}<div class="t">l</div>{nested.format("y")}</div></div>',
    )
    assert _scroll_widths(html)[1280][0] > 1280

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs
    assert not any("div#y" in r for r in repairs)
    assert _scroll_widths(repaired)[1280] == (1280, 1280)
    assert _grid_columns(repaired, "#x", 1280) == 2
    assert _grid_columns(repaired, "#y", 1280) == 2


def test_grid_whose_tracks_do_not_fit_even_without_gaps_still_collapses_to_one_column() -> None:
    html = _page(
        ".g { display: grid; grid-template-columns: repeat(2, 600px); gap: 16px; } .t { height: 40px; }",
        '<div class="page"><div class="g"><div class="t">a</div><div class="t">b</div></div></div>',
    )

    repaired = _assert_repaired(html, "div.g { grid-template-columns: minmax(0,1fr) !important; }", "gridcollapse")

    assert "fr) minmax" not in repaired


def test_repairs_fitting_grid_nested_in_a_qualifying_grid_by_collapsing_it() -> None:
    html = _page(
        ".outer { display: grid; grid-template-columns: 1fr 1fr; } "
        ".inner { display: grid; grid-template-columns: repeat(2, 300px); } .t { height: 40px; }",
        '<div class="page"><div class="outer"><div class="inner"><div class="t">a</div><div class="t">b</div></div><div class="t">c</div></div></div>',
    )
    assert _scroll_widths(html)[390][0] > 390

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport


def test_grid_tracks_inflated_by_content_fall_back_to_one_column() -> None:
    html = _page(
        ".g { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; } .box { width: 190px; height: 40px; }",
        '<div class="page"><div class="g"><div><div class="box"></div></div><div><div class="box"></div></div></div></div>',
    )
    assert _scroll_widths(html)[390][0] > 390

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    assert "minmax(0,1fr)" in repaired
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport


def test_gap_only_grid_repair_still_collapses_widths_below_its_media_guard() -> None:
    html = _page(_ASYM_STYLE, '<div class="page"><div class="asym"><div class="t">a</div><div class="t">b</div></div></div>')
    assert _scroll_widths(html, (1100,))[1100] == (1132, 1100)

    repaired, _ = repair_horizontal_overflow(html)

    assert _scroll_widths(repaired, (1100,))[1100] == (1100, 1100)
    assert _grid_columns(repaired, ".asym", 1280) == 2


def _scaled_card_html(scale: str) -> str:
    return _page(
        ".plans { display: grid; grid-template-columns: 1fr; gap: 16px; } "
        "@media (min-width: 1024px) { .plans { grid-template-columns: repeat(3, 1fr); padding: 0 40px; } } "
        f".plan {{ height: 80px; }} .plan.featured {{ transform: scale({scale}); }}",
        '<div class="plans"><div class="plan">a</div><div class="plan featured">b</div><div class="plan">c</div></div>',
    )


def _card_transform(html: str, width: int) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": width, "height": 800})
            page.set_content(html)
            return page.evaluate("() => getComputedStyle(document.querySelector('.featured')).transform")
        finally:
            browser.close()


def test_repairs_scaled_card_pushed_past_viewport_edge_in_single_column_layout() -> None:
    html = _scaled_card_html("1.05")
    assert _scroll_widths(html)[390][0] > 390
    assert _scroll_widths(html)[1280] == (1280, 1280)

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("unscale: ") for r in repairs)
    assert any(r.endswith("@390px") for r in repairs)
    assert "transform: none !important" in repaired
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport
    assert _card_transform(repaired, 390) == "none"
    assert _card_transform(repaired, 1280) == _card_transform(html, 1280) != "none"


def test_leaves_scaled_element_that_does_not_overflow_unchanged() -> None:
    html = _page(
        ".card { width: 200px; margin: 40px; height: 80px; transform: scale(1.1); }",
        '<div class="card">a</div>',
    )
    assert all(scroll == viewport for scroll, viewport in _scroll_widths(html).values())

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == []
    assert repaired == html


def test_leaves_scaled_element_wider_than_viewport_unchanged() -> None:
    html = _page(
        ".card { width: 120vw; height: 80px; transform: scale(1.05); }",
        '<div class="card">a</div>',
    )
    assert _scroll_widths(html)[390][0] > 390

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == []
    assert repaired == html


def test_vertical_scale_on_wrapper_does_not_stop_grid_repair_beneath_it() -> None:
    html = _page(
        ".wrap { width: calc(100% + 0.6px); transform: scale(1, 1.05); } .g { display: grid; grid-template-columns: repeat(3, 300px); } .t { height: 40px; }",
        '<div class="wrap"><div class="g"><div class="t">a</div><div class="t">b</div><div class="t">c</div></div></div>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    assert _scroll_widths(repaired)[390] == (391, 390)
    assert _scroll_widths(repaired)[768] == (769, 768)


def test_scaled_wrapper_within_one_pixel_of_edge_does_not_stop_grid_repair_beneath_it() -> None:
    html = _page(
        ".wrap { width: calc(100% - 7px); transform: scale(1.01); transform-origin: left; } .g { display: grid; grid-template-columns: repeat(3, 300px); } .t { height: 40px; }",
        '<div class="wrap"><div class="g"><div class="t">a</div><div class="t">b</div><div class="t">c</div></div></div>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert any(r.startswith("gridcollapse: ") and r.endswith("@390px") for r in repairs)
    assert any(r.startswith("gridcollapse: ") and r.endswith("@768px") for r in repairs)
    assert not any(r.startswith("unscale: ") for r in repairs)


_IMAGE = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='1024' height='1024'/>"


def _navwrap_single_flex_item_html() -> str:
    return _page(
        ".nav-container { display: flex; flex-wrap: nowrap; align-items: center; justify-content: space-between; padding: 0 16px; } "
        ".logo { flex: none; width: 60px; } "
        "nav { display: block; } "
        "nav ul { display: flex; flex-wrap: nowrap; gap: 24px; list-style: none; margin: 0; } "
        "nav a { display: block; width: 60px; } "
        "nav a.btn { display: inline-flex; }",
        '<header><div class="container nav-container"><div class="logo">L</div>'
        '<nav><ul><li><a href="#">a</a></li><li><a href="#">b</a></li><li><a href="#">c</a></li>'
        '<li><a href="#">d</a></li><li><a class="btn" href="#">e</a></li></ul></nav></div></header>',
    )


def test_navwrap_wraps_row_with_several_items_rather_than_single_item_inline_flex_link() -> None:
    _assert_repaired(_navwrap_single_flex_item_html(), "ul { flex-wrap: wrap !important; max-width: 100% !important; }", "navwrap")


def test_navwrap_counts_text_node_as_flex_item_next_to_single_element() -> None:
    html = _page(
        ".wrap { display: grid; } .cta { display: flex; } .cta button { flex: none; width: 340px; }",
        '<div class="wrap"><div class="card"><div class="cta"><button>Go</button>'
        "Subscribe to our weekly newsletter for the latest updates and announcements</div></div></div>",
    )
    _assert_repaired(html, "flex-wrap: wrap !important", "navwrap")


def _text_node_flex_item_html(container_style: str = "", extra: str = "") -> str:
    items = "".join(f'<li><span class="year">{year}</span> Han, J. "Trust Calibration in Human-Robot Interaction." HRI.</li>' for year in (2027, 2026, 2025))
    return _page(
        ".list { list-style: none; padding: 0; margin: 0; } "
        f".list li {{ display: flex; align-items: baseline; padding: 1rem 0; {container_style} }} "
        ".year { min-width: 140px; margin-right: 1rem; } "
        "@media (max-width: 800px) { .year { min-width: 100%; } }",
        f'<ul class="list">{items}</ul>{extra}',
    )


def test_navwrap_wraps_flex_container_whose_text_node_item_is_pushed_past_the_edge() -> None:
    html = _text_node_flex_item_html()
    before_height = _image_size(html, 1280, "li")

    repaired = _assert_repaired(html, "@media (max-width: 1279px) { ul.list > li { flex-wrap: wrap !important; max-width: 100% !important; } }", "navwrap")

    assert _image_size(repaired, 1280, "li") == before_height


def test_navwrap_ignores_text_node_overflow_inside_scroll_container() -> None:
    html = _text_node_flex_item_html("overflow-x: auto;", '<div style="width: 600px; height: 10px"></div>')

    repaired, repairs = repair_horizontal_overflow(html)

    assert repaired == html
    assert repairs == []


_LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis" * 2


def _assert_untouched(html: str) -> None:
    repaired, repairs = repair_horizontal_overflow(html)

    assert repaired == html
    assert repairs == []


def test_navwrap_leaves_flex_text_that_wrapping_cannot_fit() -> None:
    _assert_untouched(_page(".solo { display: flex; }", f'<section><p class="solo">{_LONG_WORD}</p></section>'))


def test_navwrap_text_candidate_does_not_replace_overflowing_child_candidate() -> None:
    html = _page(
        ".n { display: flex; gap: 8px; white-space: nowrap; }",
        f'<header><nav class="n"><a class="lnk" href="#">{_LONG_WORD}</a> trailing menu text that is long enough</nav></header>',
    )

    _assert_untouched(html)


def test_navwrap_does_not_restyle_sibling_that_does_not_overflow() -> None:
    sibling = '<li><span class="a">Alpha beta gamma delta epsilon zeta eta theta iota kappa</span><span class="b">Lambda mu nu xi omicron pi rho sigma tau upsilon phi</span></li>'

    _assert_untouched(_text_node_flex_item_html().replace("</ul>", f"{sibling}</ul>"))


def test_navwrap_ignores_text_node_overflow_under_clipping_ancestor() -> None:
    html = _text_node_flex_item_html().replace('<ul class="list">', '<div style="overflow: hidden"><ul class="list">').replace(
        "</ul>", '</ul></div><div style="width: 600px; height: 10px"></div>'
    )

    _assert_untouched(html)


def test_gridcollapse_when_nowrap_flex_heading_word_is_wider_than_its_grid_track() -> None:
    html = _page(
        ".wrap { padding: 0; } .g { display: grid; grid-template-columns: minmax(0, 1fr); gap: 32px; } "
        ".t { display: flex; align-items: center; gap: 12px; font: 32px monospace; margin: 0; } "
        "@media (min-width: 700px) { .wrap { padding: 0 64px; } .g { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 64px; } }",
        '<div class="wrap"><div class="g"><div><h2 class="t">Background</h2></div><div><h2 class="t">Internationalizationally</h2></div></div></div>',
    )

    repaired = _assert_repaired(html, "@media (max-width: 1279px) { div.g { grid-template-columns: minmax(0,1fr) !important; } }", "gridcollapse")

    assert "flex-wrap" not in repaired


def test_unrepairable_flex_text_overflow_does_not_block_nav_repair_with_same_tag_label() -> None:
    items = "<div>Menu item</div>" * 5
    html = _page(
        ".n { display: flex; gap: 24px; } .n div { flex: none; padding: 0 20px; }",
        f'<header><nav class="n">{items}</nav></header><section><div style="display: flex">{_LONG_WORD}</div></section>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == ["navwrap: div @390px"]
    assert "nav.n { flex-wrap: wrap !important;" in repaired


def _image_size(html: str, width: int, selector: str) -> tuple[float, float]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": width, "height": 800})
            page.set_content(html)
            return tuple(page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect(); return [r.width, r.height]; }", selector))
        finally:
            browser.close()


def _media_grid_html() -> str:
    return _page(
        ".container { padding: 0 16px; } .location-grid { display: grid; gap: 16px; }",
        '<section class="container"><div class="location-grid"><div class="location-info"><p>address</p></div>'
        f'<div class="location-image-container"><img class="location-image" src="{_IMAGE}" width="480" height="360"></div></div></section>',
    )


def test_repairs_unconstrained_image_stretching_auto_grid_track() -> None:
    _assert_repaired(_media_grid_html(), "img.location-image { max-width: 100% !important; height: auto !important; }", "media")


def _media_capped_html() -> str:
    return _page(
        ".container { padding: 0 16px; } .hero { display: grid; grid-template-columns: 1fr; } "
        ".hero-visual { display: flex; justify-content: center; } .hero-visual img { max-width: 400px; height: auto; display: block; }",
        f'<section class="container hero"><div class="hero-content"><p>text</p></div><div class="hero-visual"><img src="{_IMAGE}" width="1024" height="1024"></div></section>',
    )


def test_repairs_image_with_fixed_pixel_max_width_stretching_grid_track() -> None:
    _assert_repaired(_media_capped_html(), "img { max-width: min(400px, 100%) !important; height: auto !important; }", "media")


def test_media_repair_does_not_distort_image_with_fixed_css_height() -> None:
    html = _page(".pic { width: 600px; height: 400px; }", f'<div><img class="pic" src="{_IMAGE}"></div>')

    repaired, _ = repair_horizontal_overflow(html)

    width, height = _image_size(repaired, 390, "img.pic")
    assert repaired == html or abs(width / height - 1.5) < 0.01


def test_media_repair_does_not_change_image_at_widths_that_were_not_overflowing() -> None:
    html = _page(
        "@media (max-width: 500px) { img.hero { max-width: 300px; } }",
        f'<div><img class="hero" src="{_IMAGE}" width="900" height="900"></div>',
    )
    before = _image_size(html, 390, "img.hero")

    repaired, _ = repair_horizontal_overflow(html)

    assert _image_size(repaired, 390, "img.hero") == before
    assert repaired == html


def test_media_repair_fixes_second_image_below_an_already_repaired_one() -> None:
    html = _page(
        "",
        f'<div><img class="a" src="{_IMAGE}" width="900" height="900"></div>'
        f'<div><img class="b" src="{_IMAGE}" width="480" height="480"></div>',
    )

    repaired = _assert_repaired(html, "img.b { max-width: 100% !important;", "media")

    assert "img.a { max-width: 100% !important;" in repaired


def test_wraps_classless_inline_flex_row_of_fixed_size_images_among_sibling_divs() -> None:
    images = "".join(f'<img src="{_IMAGE}" width="480" height="360">' for _ in range(3))
    html = _page(
        "img { max-width: 100%; height: auto; display: block; }",
        '<section id="menu"><div class="container"><div><p>intro</p></div>'
        f'<div style="display: flex; gap: 16px">{images}</div>'
        "<div><p>outro</p></div></div></section>",
    )

    _assert_repaired(html, "div.container > div:nth-child(2) { flex-wrap: wrap !important;", "navwrap")


def test_flex_card_grid_without_unique_selector_still_gets_gridcollapse_instead_of_positional_navwrap() -> None:
    html = _page(
        ".grid { display: grid; grid-template-columns: 300px 300px; gap: 16px; } .grid > div { display: flex; }",
        '<section><div class="grid"><div><span>a</span><span>b</span></div><div><span>c</span><span>d</span></div></div></section>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == ["gridcollapse: div @390px"]
    assert "div.grid { grid-template-columns: minmax(0,1fr) !important; }" in repaired


def test_one_pixel_overflowing_flex_rows_without_unique_selector_still_let_walk_reach_grid_beneath() -> None:
    row = '<div class="o">{}</div>'
    grid = '<div class="b"><div class="grid"><div>a</div><div>b</div></div></div>'
    html = _page(
        ".o { display: flex; width: calc(100vw + 1px); } .b { flex: 1; min-width: 0; } "
        ".grid { display: grid; grid-template-columns: 300px 300px; gap: 16px; }",
        f"<section><div>{row.format(grid)}{row.format('')}</div></section>",
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs and all(r.startswith("gridcollapse: ") for r in repairs)
    assert "div.grid { grid-template-columns: minmax(0,1fr) !important; }" in repaired
    assert "flex-wrap" not in repaired


def test_logs_viewport_and_offenders_when_overflow_has_no_repair_candidate(caplog: pytest.LogCaptureFixture) -> None:
    html = _page("div.wide { width: 600px; }", '<div class="wide">wide</div>')

    with caplog.at_level("WARNING", logger="wra.generator.overflow_repair"):
        assert repair_horizontal_overflow(html) == (html, [])

    assert "390px (div.wide)" in caplog.text


def test_leaves_deeply_nested_fixed_width_overflow_in_body_child_grid_unchanged() -> None:
    html = _page(
        ".grid { display: grid; grid-template-columns: 200px 1fr; }",
        '<div class="grid"><div class="col1"></div>'
        '<div class="a"><div class="b"><div class="c"><div class="d" style="width: 600px; height: 20px;">deep</div></div></div></div>'
        "</div>",
    )

    assert repair_horizontal_overflow(html) == (html, [])


def test_leaves_single_column_grid_with_bracketed_line_names_unchanged() -> None:
    html = _page(
        ".wrap { display: grid; grid-template-columns: [full-start] 1fr [full-end]; }",
        '<div class="wrap"><div class="wide" style="width: 600px; height: 20px;">wide</div></div>',
    )

    assert repair_horizontal_overflow(html) == (html, [])


_TABLE_STYLE = "table.plans { width: 100%; border-collapse: collapse; } table.plans td { padding: 40px; }"
_TABLE_ROW = '<tr><td>Starter</td><td>Professional</td><td>Enterprise</td></tr>'


def test_repairs_wide_table_by_letting_it_scroll_at_the_overflowing_width() -> None:
    html = _page(_TABLE_STYLE, f'<section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>')
    assert _scroll_widths(html)[390][0] > 390
    assert all(scroll == viewport for width, (scroll, viewport) in _scroll_widths(html).items() if width != 390)

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == ["table: table.plans @390px", "scrollcue: table.plans @390px"]
    assert "table.plans { display: block !important; max-width: 100% !important; overflow-x: auto !important; }" in repaired
    assert "@media (max-width: 767px)" in repaired
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport


def test_wide_image_in_table_cell_gets_media_repair_not_table_repair() -> None:
    html = _page(
        "table.plans { width: 100%; border-collapse: collapse; }",
        f'<section id="pricing"><table class="plans"><tr><td><img src="{_IMAGE}" style="width: 600px;"></td><td>Pro</td></tr></table></section>',
    )
    assert _scroll_widths(html)[390][0] > 390

    repaired, repairs = repair_horizontal_overflow(html)

    assert any(r.startswith("media: ") for r in repairs)
    assert not any(r.startswith("table: ") for r in repairs)
    assert _scroll_widths(repaired)[390][0] == 390


def test_wide_table_with_icon_past_the_edge_still_gets_table_repair() -> None:
    html = _page(
        _TABLE_STYLE,
        f'<section id="pricing"><table class="plans"><tr><td>Starter</td><td>Professional</td><td>Enterprise</td><td>Unlimited</td><td><img class="badge" width="24" height="24" src="{_IMAGE}"></td></tr></table></section>',
    )
    assert _scroll_widths(html)[390][0] > 390

    repaired, repairs = repair_horizontal_overflow(html)

    assert any(r.startswith("table: ") for r in repairs)
    assert _scroll_widths(repaired)[390][0] == 390


def test_leaves_table_overflowing_by_less_than_one_pixel_unchanged() -> None:
    html = _page("table.plans { width: 390.6px; }", '<section id="pricing"><table class="plans"><tr><td>a</td></tr></table></section>')

    assert repair_horizontal_overflow(html) == (html, [])


def test_leaves_table_inside_scroll_wrapper_unchanged() -> None:
    html = _page(
        _TABLE_STYLE + " .scroll { overflow-x: auto; }",
        f'<div class="scroll"><table class="plans">{_TABLE_ROW}</table></div>',
    )

    assert repair_horizontal_overflow(html) == (html, [])


_CUE_PROBE = """async (selector) => {
  const el = document.querySelector(selector);
  const frames = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const read = async left => {
    el.scrollLeft = left;
    await frames();
    const style = getComputedStyle(el);
    return [style.getPropertyValue('--wra-cue-l'), style.getPropertyValue('--wra-cue-r')];
  };
  const animations = el.getAnimations().length;
  const start = await read(0);
  const end = await read(el.scrollWidth);
  return { animations, start, end, animationName: getComputedStyle(el).animationName, animationNames: el.getAnimations().map(a => a.animationName), pageWidth: document.documentElement.scrollWidth };
}"""


def _cue_probe(html: str, width: int, selector: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.set_content(html)
            return page.evaluate(_CUE_PROBE, selector)
        finally:
            browser.close()


def _cues(repairs: list[str]) -> list[str]:
    return [r for r in repairs if r.startswith("scrollcue: ")]


def test_table_turned_into_a_scroller_gets_an_edge_fade_cue_driven_by_its_scroll_position() -> None:
    html = _page(_TABLE_STYLE, f'<section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>')

    repaired, repairs = repair_horizontal_overflow(html)

    assert _cues(repairs) == ["scrollcue: table.plans @390px"]
    assert repaired.count("@keyframes wra-scroll-cue") == 1
    probe = _cue_probe(repaired, 390, "table.plans")
    assert probe["animations"] == 1
    assert probe["start"] == ["0px", "24px"]
    assert probe["end"] == ["24px", "0px"]
    assert probe["pageWidth"] == 390


def test_cue_goes_to_the_inner_scroller_revealed_by_a_shrink_repair_not_to_the_shrink_target() -> None:
    links = "".join('<a href="#">Documentation</a> ' for _ in range(14))
    html = _page(
        ".row { display: flex; } .col { flex: 1; } .scroller { overflow-x: auto; white-space: nowrap; }",
        f'<main><div class="row"><div class="col"><div class="scroller">{links}</div></div></div></main>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert any(r.startswith("shrink: ") for r in repairs)
    assert _cues(repairs)
    assert all(r.startswith("scrollcue: div.scroller @") for r in _cues(repairs))
    assert _cue_probe(repaired, 390, "div.scroller")["animations"] == 1
    assert _cue_probe(repaired, 390, "div.col")["animations"] == 0


def test_scroller_that_already_overflowed_in_the_baseline_gets_no_cue() -> None:
    items = "".join(f"<span>Item number {i}</span> " for i in range(30))
    html = _page(
        _TABLE_STYLE + " .strip { overflow-x: auto; white-space: nowrap; }",
        f'<div class="strip">{items}</div><section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert _cues(repairs) == ["scrollcue: table.plans @390px"]
    assert _cue_probe(repaired, 390, "div.strip")["animations"] == 0


def test_scroller_with_its_own_animation_gets_no_cue_and_keeps_the_animation() -> None:
    html = _page(
        _TABLE_STYLE + " @keyframes pulse { from { opacity: .5; } to { opacity: 1; } } table.plans { animation: pulse 2s infinite; }",
        f'<section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == ["table: table.plans @390px"]
    assert "wra-scroll-cue" not in repaired
    assert _cue_probe(repaired, 390, "table.plans")["animationName"] == "pulse"


def test_scroller_with_its_own_mask_image_gets_no_cue() -> None:
    html = _page(
        _TABLE_STYLE + " table.plans { mask-image: linear-gradient(#000, #000); }",
        f'<section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == ["table: table.plans @390px"]
    assert "wra-scroll-cue" not in repaired


def test_cue_for_a_narrow_only_scroller_is_not_applied_at_wider_viewports() -> None:
    html = _page(_TABLE_STYLE, f'<section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>')

    repaired, _ = repair_horizontal_overflow(html)

    assert _cue_probe(repaired, 390, "table.plans")["animations"] == 1
    assert _cue_probe(repaired, 1280, "table.plans")["animations"] == 0


def test_cue_does_not_reach_a_width_where_the_scroller_already_scrolled_with_its_own_animation() -> None:
    links = "".join('<a href="#">Documentation</a> ' for _ in range(14))
    html = _page(
        ".row { display: flex; } .col { flex: 1; } .scroller { overflow-x: auto; white-space: nowrap; } @keyframes pulse { from { opacity: .5; } to { opacity: 1; } } "
        "@media (max-width: 500px) { .scroller { max-width: 380px; animation: pulse 2s infinite; } }",
        f'<main><div class="row"><div class="col"><div class="scroller">{links}</div></div></div></main>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert _cues(repairs) == ["scrollcue: div.scroller @768px", "scrollcue: div.scroller @1280px"]
    narrow = _cue_probe(repaired, 390, "div.scroller")
    assert narrow["animationName"] == "pulse"
    assert narrow["animationNames"] == ["pulse"]
    assert _cue_probe(repaired, 768, "div.scroller")["animationNames"] == ["wra-scroll-cue"]


def test_cue_does_not_clip_the_box_shadow_above_and_below_the_scroller() -> None:
    from playwright.sync_api import sync_playwright

    html = _page(
        _TABLE_STYLE + " section { padding: 40px 20px; } table.plans td { background: #36c; } table.plans { box-shadow: 0 0 0 10px red; }",
        f'<section id="pricing"><table class="plans">{_TABLE_ROW}</table></section>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert _cues(repairs) == ["scrollcue: table.plans @390px"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.set_content(repaired)
            page.wait_for_timeout(300)
            left, top, height = page.evaluate("() => { const r = document.querySelector('table.plans').getBoundingClientRect(); return [r.left, r.top, r.height]; }")
            strips = [{"x": left + 20, "y": top - 7, "width": 40, "height": 4}, {"x": left + 20, "y": top + height + 3, "width": 40, "height": 4}]
            with_cue = [page.screenshot(clip=strip) for strip in strips]
            page.evaluate("() => { const s = document.querySelector('table.plans').style; s.setProperty('mask-image', 'none', 'important'); s.setProperty('-webkit-mask-image', 'none', 'important'); }")
            page.wait_for_timeout(300)
            without_cue = [page.screenshot(clip=strip) for strip in strips]
        finally:
            browser.close()
    assert with_cue == without_cue


def test_leaves_html_unchanged_without_overflow() -> None:
    html = _page("section { padding: 1rem; }", "<section><p>content</p></section>")

    assert repair_horizontal_overflow(html) == (html, [])


def test_leaves_unclassified_overflow_unchanged() -> None:
    html = _page("div.wide { width: 600px; }", '<div class="wide">wide</div>')

    assert repair_horizontal_overflow(html) == (html, [])


_EMAIL = "someone.with.a.very.long.name@a.really.long.company.domain.example.com"


def test_repairs_unbreakable_email_heading_inside_single_column_grid_item() -> None:
    html = _page(
        ".g { display: grid; grid-template-columns: 1fr; } h2 { font: 24px monospace; margin: 0; }",
        f'<section><div class="g"><div><h2>{_EMAIL}</h2></div></div></section>',
    )

    _assert_repaired(html, "overflow-wrap: anywhere !important", "shrink")


def test_repairs_flex_item_sized_by_a_nowrap_link_scroller() -> None:
    links = "".join('<a href="#">Documentation</a> ' for _ in range(14))
    html = _page(
        ".row { display: flex; } .col { flex: 1; } .scroller { overflow-x: auto; white-space: nowrap; }",
        f'<main><div class="row"><div class="col"><div class="scroller">{links}</div></div></div></main>',
    )

    repaired = _assert_repaired(html, "div.col { min-width: 0 !important; max-width: 100% !important; }", "shrink")

    assert "overflow-wrap" not in repaired


_PADDED_CARD_STYLE = ".row { display: flex; } .card { flex: 1 1 0; box-sizing: border-box; padding: 0 32px; font: 16px 'Courier New'; } .side { flex: 0 0 100px; background: #ccc; }"
_PADDED_EMAIL = "a" * 23 + "@example.com"


def test_wraps_unbreakable_email_that_would_spill_into_the_padding_of_a_shrunk_card() -> None:
    html = _page(_PADDED_CARD_STYLE, f'<main><div class="row"><div class="card">{_PADDED_EMAIL}</div></div></main>')

    _assert_repaired(html, "overflow-wrap: anywhere !important", "shrink")


def test_wraps_unbreakable_email_that_would_overlap_a_fixed_sibling_of_a_shrunk_card() -> None:
    html = _page(_PADDED_CARD_STYLE, f'<main><div class="row"><div class="card">{_PADDED_EMAIL}</div><div class="side">side</div></div></main>')

    _assert_repaired(html, "overflow-wrap: anywhere !important", "shrink")


def test_wraps_email_at_narrow_widths_the_shrink_rule_reaches_though_only_wide_ones_overflowed() -> None:
    from playwright.sync_api import sync_playwright

    links = "".join(f'<a href="#">link number {i}</a> ' for i in range(12))
    html = _page(
        ".row { display: flex; } .card { flex: 1 1 0; font: 16px 'Courier New'; } .other { flex: 1 1 0; background: #ccc; } "
        ".scroller { display: none; overflow-x: auto; white-space: nowrap; } @media (min-width: 700px) { .scroller { display: block; } }",
        f'<main><div class="row"><div class="card"><p class="mail">{"a" * 14}@example.com</p><div class="scroller">{links}</div></div><div class="other">x y z</div></div></main>',
    )
    assert _scroll_widths(html)[390][0] == 390

    repaired = _assert_repaired(html, "overflow-wrap: anywhere !important", "shrink")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.set_content(repaired)
            text_right, card_right = page.evaluate(
                "() => { const r = document.createRange(); r.selectNodeContents(document.querySelector('.mail').firstChild); return [r.getBoundingClientRect().right, document.querySelector('.card').getBoundingClientRect().right]; }"
            )
        finally:
            browser.close()
    assert text_right <= card_right + 1


_CONTACT_STYLE = (
    ".grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; } "
    ".item { display: flex; flex-direction: column; min-width: 0; } .contact-link { font: 20px monospace; }"
)
_CONTACT_ITEM = '<div class="item"><a class="contact-link" href="#">https://github.com/name0825</a></div>'


def test_repairs_url_text_running_past_the_edge_of_a_fitting_link_box() -> None:
    html = _page(_CONTACT_STYLE, f'<section><div class="grid">{_CONTACT_ITEM * 3}</div></section>')
    scrolls = _scroll_widths(html)
    assert scrolls[768][0] > 768
    assert scrolls[390][0] == 390 and scrolls[1280][0] == 1280

    repaired, repairs = repair_horizontal_overflow(html)

    assert repairs == ["shrink: a.contact-link::text @768px"]
    assert "@media (max-width: 1279px)" in repaired
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport


def test_repairs_top_level_block_wider_than_viewport_because_of_min_content() -> None:
    html = _page("main { display: grid; } p { font: 24px monospace; margin: 0; }", f"<main><section><p>{_EMAIL}</p></section></main>")

    _assert_repaired(html, "section { min-width: 0 !important;", "shrink")


def test_absolutely_positioned_element_past_the_right_edge_gets_no_shrink_candidate() -> None:
    html = _page(
        ".deco { position: absolute; top: 0; left: 20px; font: 24px monospace; }",
        f'<main><p>content</p><div class="deco">{_EMAIL}</div></main>',
    )
    assert _scroll_widths(html)[390][0] > 390

    _assert_untouched(html)


def test_text_clipped_by_its_own_overflow_hidden_box_gets_no_shrink_candidate() -> None:
    html = _page(
        ".clip { overflow: hidden; width: 300px; margin: 0; }",
        f'<main><p class="clip">{_EMAIL * 3}</p><div style="width: 600px; height: 10px"></div></main>',
    )
    assert _scroll_widths(html)[390][0] > 390

    _assert_untouched(html)


def test_repairs_independent_content_sized_offenders_regardless_of_dom_order() -> None:
    html = _page(
        ".g { display: grid; grid-template-columns: 1fr; } .a { font: 16px monospace; } .b { font: 24px monospace; }",
        f'<main><div class="g"><div class="a">{_EMAIL}</div></div><div class="g"><div class="b">{_EMAIL}</div></div></main>',
    )

    repaired, repairs = repair_horizontal_overflow(html)

    assert any("div.a" in r for r in repairs) and any("div.b" in r for r in repairs)
    assert all(r.startswith("shrink: ") for r in repairs)
    for scroll, viewport in _scroll_widths(repaired).values():
        assert scroll == viewport


def test_repeated_wide_images_without_unique_selector_are_not_squashed_by_shrink() -> None:
    image = f'<div><img class="t" src="{_IMAGE}" width="800" height="400"></div>'
    html = _page("", f"<main>{image}{image}</main>")
    assert _scroll_widths(html)[390][0] > 390

    repaired, repairs = repair_horizontal_overflow(html)

    assert not any(r.startswith("shrink: ") for r in repairs)
    assert _image_size(repaired, 390, "img.t") == _image_size(html, 390, "img.t")


def test_only_shrink_may_narrow_a_top_level_block_that_passed_the_edge() -> None:
    def snapshot(width: int, block: int) -> dict:
        return {"viewport": width, "scrollWidth": max(width, block), "fits": {}, "tops": [[0, block]], "rects": {}, "offenders": []}

    baseline = {width: snapshot(width, width + 200) for width in (390, 768, 1280)}
    trial = {width: snapshot(width, width) for width in (390, 768, 1280)}

    assert _verified(baseline, trial, {"type": "shrink", "label": "section", "widths": [390]}, [])
    assert not _verified(baseline, trial, {"type": "navwrap", "label": "section", "widths": [390]}, [])
