import importlib.util

import pytest
from playwright.sync_api import sync_playwright

from wra.generator.table_wrap_repair import _insert_breaks, repair_wide_tables

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")

_EMAIL_TABLE = """<table class="people">
<tr><th>Name</th><th>Email</th></tr>
<tr><td>Kim</td><td>very.long.address.for.someone@subdomain.example-company.com</td></tr>
<tr><td>Lee</td><td>another.long.address.for.someone@subdomain.example-company.com</td></tr>
</table>"""


def _page(body: str, style: str = "") -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ margin: 0; }}
  table {{ border-collapse: collapse; }}
  td, th {{ padding: 8px; border: 1px solid #888; }}
  {style}
</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>"""


def _wrapped(table: str, overflow: str) -> str:
    return _page(f'<div class="wrap">{table}</div>', f".wrap {{ overflow-x: {overflow}; }}")


def _table_overflow(html: str, width: int) -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 800})
        page.set_content(html)
        result = page.evaluate("() => { const t = document.querySelector('table'); return Math.round(t.getBoundingClientRect().width) - t.parentElement.clientWidth; }")
        browser.close()
    return result


def _text_content(html: str) -> str:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.set_content(html)
        result = page.evaluate("() => document.body.textContent")
        browser.close()
    return result


@pytest.mark.parametrize("overflow", ["auto", "hidden"])
def test_email_table_is_repaired_with_wbr_breaks(overflow):
    html = _wrapped(_EMAIL_TABLE.replace("<td>very", '<td><a href="mailto:a@b.co">very').replace("-company.com</td>", "-company.com</a></td>", 1), overflow)
    assert _table_overflow(html, 390) > 1

    repaired, repairs = repair_wide_tables(html)

    assert repaired != html
    assert len(repairs) == 1 and "(wbr breaks)" in repairs[0] and "@390" in repairs[0]
    assert _table_overflow(repaired, 390) <= 1
    assert "@<wbr>subdomain.<wbr>example-company.<wbr>com" in repaired
    assert 'href="mailto:a@b.co"' in repaired
    assert "@media" not in repaired.split("</style>")[1] and "table-wrap" not in repaired
    assert _text_content(repaired) == _text_content(html)


def test_wbr_plus_css_when_segment_is_still_too_wide():
    segment = "x" * 60
    table = f"<table><tr><th>Name</th><th>Email</th></tr><tr><td>Kim</td><td>{segment}@{segment}.com</td></tr></table>"
    html = _wrapped(table, "auto")

    repaired, repairs = repair_wide_tables(html)

    assert len(repairs) == 1 and "(wbr + long-token cells)" in repairs[0]
    assert f"{segment}@<wbr>{segment}.<wbr>com" in repaired
    assert _table_overflow(repaired, 390) <= 1
    assert _text_content(repaired) == _text_content(html)


def test_css_only_when_token_has_no_break_points():
    table = f"<table><tr><th>Name</th><th>Code</th></tr><tr><td>Kim</td><td>{'x' * 80}</td></tr></table>"
    html = _wrapped(table, "auto")

    repaired, repairs = repair_wide_tables(html)

    assert len(repairs) == 1 and "(long-token cells)" in repairs[0]
    assert "<wbr>" not in repaired
    assert _table_overflow(repaired, 390) <= 1


def test_insert_breaks_skips_raw_text_elements():
    html = "<table><tr><td>a@b.com <textarea>c@d.com</textarea> <noscript>e@f.com</noscript></td></tr></table>"

    assert _insert_breaks(html, 0, [0]) == html.replace("a@b.com", "a@<wbr>b.<wbr>com")


def test_insert_breaks_preserves_entities_crlf_and_implicit_cell_end():
    html = "<table>\r\n<tr><td>x&amp;y@z.com\r\n<td>keep@me.com\r\n</table>\r\n"

    assert _insert_breaks(html, 0, [0]) == html.replace("y@z.com", "y@<wbr>z.<wbr>com")
    assert _insert_breaks(html, 0, [1]) == html.replace("keep@me.com", "keep@<wbr>me.<wbr>com")


def test_fitting_table_is_unchanged():
    html = _wrapped("<table><tr><td>Name</td><td>Email</td></tr></table>", "auto")

    assert repair_wide_tables(html) == (html, [])


def test_wrapping_cannot_help_short_word_columns():
    cells = "".join(f"<td>{word}</td>" for word in ["alpha", "bravo", "delta", "gamma", "omega", "sigma", "theta", "kappa"])
    html = _wrapped(f'<table><tr>{cells}</tr></table>', "auto").replace("td, th { padding: 8px;", "td, th { white-space: nowrap; padding: 8px;")

    assert repair_wide_tables(html) == (html, [])


def test_table_in_closed_details_is_unchanged():
    html = _wrapped(f"<details>{_EMAIL_TABLE}</details>", "auto")

    assert repair_wide_tables(html) == (html, [])


def test_table_wide_only_on_desktop_is_unchanged():
    html = _wrapped("<table><tr><td>a@b.co</td><td>Name</td></tr></table>", "auto").replace(".wrap {", "@media (min-width: 900px) { .wrap { width: 60px; } } .wrap {")

    assert repair_wide_tables(html) == (html, [])


def test_second_run_is_idempotent():
    repaired, _ = repair_wide_tables(_wrapped(_EMAIL_TABLE, "auto"))

    assert repair_wide_tables(repaired) == (repaired, [])
