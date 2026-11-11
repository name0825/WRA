import importlib.util

import pytest
from playwright.sync_api import sync_playwright

from wra.generator.table_wrap_repair import repair_wide_tables

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


@pytest.mark.parametrize("overflow", ["auto", "hidden"])
def test_email_table_is_repaired(overflow):
    html = _wrapped(_EMAIL_TABLE, overflow)
    assert _table_overflow(html, 390) > 1

    repaired, repairs = repair_wide_tables(html)

    assert repaired != html
    assert len(repairs) == 1 and "long-token cells" in repairs[0] and "@390" in repairs[0]
    assert _table_overflow(repaired, 390) <= 1
    assert "@media (max-width:768px)" in repaired


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
