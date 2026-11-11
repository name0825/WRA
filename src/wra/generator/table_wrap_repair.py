import logging
from pathlib import Path

from wra.browser.guard import launch_guarded
from .fixed_trap_repair import _VIEWPORTS, _inject_style
from .overflow_repair import _SELECTOR_HELPERS
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_MOBILE_MAX = 768
_WIDTHS = tuple(width for width in _VIEWPORTS if width <= _MOBILE_MAX)

_DETECT_SCRIPT = (
    "() => {\n"
    + _SELECTOR_HELPERS
    + r"""
  const LONG_TOKEN = /\S{16,}|@|:\/\//;
  const out = [];
  for (const table of document.querySelectorAll('table')) {
    if (table.getClientRects().length === 0) continue;
    const tableStyle = getComputedStyle(table);
    if (tableStyle.visibility === 'hidden' || tableStyle.display === 'none') continue;
    if (tableStyle.overflowX !== 'visible') continue;
    if (table.closest('details:not([open])')) continue;
    if (table.checkVisibility && !table.checkVisibility({ contentVisibilityAuto: true, visibilityProperty: true })) continue;
    let node = table.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      if (getComputedStyle(node).overflowX !== 'visible') break;
      node = node.parentElement;
    }
    if (!node || node === document.body || node === document.documentElement) continue;
    if (node.clientHeight === 0) continue;
    if (Math.round(table.getBoundingClientRect().width) <= node.clientWidth + 1) continue;
    const selector = uniqueSelector(table) || positionalSelector(table);
    if (!selector) continue;
    const cells = [];
    for (const cell of table.querySelectorAll('td, th')) {
      if (!LONG_TOKEN.test(cell.textContent)) continue;
      const cellSelector = positionalSelector(cell);
      if (cellSelector) cells.push(cellSelector);
    }
    out.push({ selector, cells });
  }
  return out;
}"""
)

_MEASURE_SCRIPT = r"""(selector) => {
  const table = document.querySelector(selector);
  if (!table) return null;
  let node = table.parentElement;
  while (node && node !== document.body && node !== document.documentElement) {
    if (getComputedStyle(node).overflowX !== 'visible') break;
    node = node.parentElement;
  }
  const hasContainer = node && node !== document.body && node !== document.documentElement;
  const tableWidth = Math.round(table.getBoundingClientRect().width);
  let squeezed = 0;
  const walker = document.createTreeWalker(table, NodeFilter.SHOW_TEXT);
  let text;
  while ((text = walker.nextNode())) {
    const content = text.textContent.trim();
    if (content.length < 3) continue;
    const parent = text.parentElement;
    if (!parent || ['script', 'style'].includes(parent.tagName.toLowerCase())) continue;
    const range = document.createRange();
    range.selectNodeContents(text);
    const lineCount = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0).length;
    if (lineCount < 2 || lineCount <= content.split(/\s+/).length) continue;
    const charCount = content.replace(/\s+/g, '').length;
    if ((charCount >= 8 && charCount / lineCount <= 4) || (charCount >= 3 && lineCount >= 3 && charCount / lineCount <= 1.5)) squeezed++;
  }
  return {
    tableWidth,
    overflow: hasContainer ? tableWidth - node.clientWidth : 0,
    squeezed,
    scrollWidth: document.documentElement.scrollWidth,
  };
}"""


def _detect(page, html: str, load) -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for width in _WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        for found in page.evaluate(_DETECT_SCRIPT):
            tables.setdefault(found["selector"], found["cells"])
    return tables


def _measure(page, html: str, selector: str, load) -> dict[int, dict] | None:
    snapshots: dict[int, dict] = {}
    for width in _WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        snapshot = page.evaluate(_MEASURE_SCRIPT, selector)
        if snapshot is None:
            return None
        snapshots[width] = snapshot
    return snapshots


def _verified(before: dict[int, dict], after: dict[int, dict] | None) -> bool:
    if after is None:
        return False
    for width, base in before.items():
        trial = after[width]
        if base["overflow"] > 1:
            if trial["overflow"] >= base["overflow"]:
                return False
        elif trial["overflow"] > base["overflow"]:
            return False
        if trial["scrollWidth"] > base["scrollWidth"] or trial["squeezed"] > base["squeezed"]:
            return False
    return any(base["overflow"] > 1 for base in before.values())


def _options(selector: str, cells: list[str]) -> list[tuple[str, str]]:
    options = []
    if cells:
        options.append(("long-token cells", f"{','.join(cells)}{{overflow-wrap:anywhere;word-break:normal}}"))
    options.append(("whole table", f"{selector} :is(td,th){{overflow-wrap:anywhere}}"))
    return options


def repair_wide_tables(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Let long unbroken text wrap in tables wider than their clipping container on mobile.

    Finds, at each mobile viewport width, tables wider than their nearest
    ancestor with a non-visible overflow-x (the clipped or scrollable tables
    the evaluator flags), and tries scoped `overflow-wrap:anywhere` CSS inside
    a max-width media query: first only on cells holding a long unbroken token
    (email, URL, 16+ characters), then on every cell of the table. An option is
    kept only if re-rendering shows the table's overflow strictly smaller where
    it overflowed and no larger anywhere, the page scroll width no larger, and
    no more cells squeezed to a few characters per line. Returns the input
    unchanged if nothing verified or Playwright is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping wide table repair")
        return html, []

    current = html
    repairs: list[str] = []
    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                tables = _detect(page, html, load)
                for selector, cells in tables.items():
                    before = _measure(page, current, selector, load)
                    if before is None:
                        continue
                    for label, css in _options(selector, cells):
                        trial = _inject_style(current, f"@media (max-width:{_MOBILE_MAX}px){{{css}}}", "table-wrap")
                        after = _measure(page, trial, selector, load)
                        if _verified(before, after):
                            widths = ", ".join(
                                f"{before[width]['tableWidth']}->{after[width]['tableWidth']} @{width}"
                                for width in before
                                if before[width]["overflow"] > 1
                            )
                            repairs.append(f"{selector}: {widths} ({label})")
                            current = trial
                            break
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping wide table repair", exc_info=True)
        return html, []

    return current, repairs
