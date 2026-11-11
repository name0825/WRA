import logging
import re
from html.parser import HTMLParser
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
    const indexes = [];
    Array.from(table.querySelectorAll('td, th')).forEach((cell, index) => {
      if (!LONG_TOKEN.test(cell.textContent)) return;
      const cellSelector = positionalSelector(cell);
      if (!cellSelector) return;
      cells.push(cellSelector);
      indexes.push(index);
    });
    out.push({ selector, cells, indexes, table: Array.from(document.querySelectorAll('table')).indexOf(table) });
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


_LONG_TOKEN = re.compile(r"\S{16,}|@|://")
_BREAK_POINT = re.compile(r"://|[@./]")
_TABLE_PARTS = ("tr", "thead", "tbody", "tfoot")
_RAW_TEXT = ("script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noscript", "plaintext")


def _add_breaks(text: str) -> str:
    def token(match: re.Match) -> str:
        word = match.group()
        if not _LONG_TOKEN.search(word):
            return word
        return _BREAK_POINT.sub(lambda point: f"{point.group()}<wbr>", word)

    return re.sub(r"\S+", token, text)


class _CellBreaker(HTMLParser):
    def __init__(self, source: str, targets: dict[int, set[int]]):
        super().__init__(convert_charrefs=False)
        self.targets = targets
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", source)]
        self.stack: list[list[int | str]] = []
        self.tables = 0
        self.raw = 0
        self.edits: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in _RAW_TEXT:
            self.raw += 1
        elif tag == "table":
            self.stack.append(["table", self.tables, 0])
            self.tables += 1
        elif tag in ("td", "th"):
            if self.stack and self.stack[-1][0] == "cell":
                self.stack.pop()
            if self.stack and self.stack[-1][0] == "table":
                top = self.stack[-1]
                self.stack.append(["cell", top[1], top[2]])
                top[2] += 1
        elif tag in _TABLE_PARTS and self.stack and self.stack[-1][0] == "cell":
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in _RAW_TEXT:
            self.raw = max(0, self.raw - 1)
        elif tag in ("td", "th") + _TABLE_PARTS:
            if self.stack and self.stack[-1][0] == "cell":
                self.stack.pop()
        elif tag == "table":
            while self.stack and self.stack.pop()[0] != "table":
                pass

    def handle_data(self, data):
        if self.raw or not self.stack or self.stack[-1][0] != "cell":
            return
        _, table, cell = self.stack[-1]
        if cell not in self.targets.get(table, ()):
            return
        broken = _add_breaks(data)
        if broken != data:
            line, column = self.getpos()
            start = self.line_starts[line - 1] + column
            self.edits.append((start, start + len(data), broken))


def _insert_breaks(html: str, table: int, cells: list[int]) -> str:
    breaker = _CellBreaker(html, {table: set(cells)})
    breaker.feed(html)
    breaker.close()
    for start, end, broken in reversed(breaker.edits):
        html = f"{html[:start]}{broken}{html[end:]}"
    return html


def _detect(page, html: str, load) -> dict[str, dict]:
    tables: dict[str, dict] = {}
    for width in _WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        for found in page.evaluate(_DETECT_SCRIPT):
            tables.setdefault(found["selector"], found)
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


def _options(selector: str, cells: list[str]) -> list[tuple[str, str | None, bool]]:
    options = []
    if cells:
        cell_css = f"{','.join(cells)}{{overflow-wrap:anywhere;word-break:normal}}"
        options.append(("wbr breaks", None, True))
        options.append(("wbr + long-token cells", cell_css, True))
        options.append(("long-token cells", cell_css, False))
    options.append(("whole table", f"{selector} :is(td,th){{overflow-wrap:anywhere}}", False))
    return options


def repair_wide_tables(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Let long unbroken text wrap in tables wider than their clipping container on mobile.

    Finds, at each mobile viewport width, tables wider than their nearest
    ancestor with a non-visible overflow-x (the clipped or scrollable tables
    the evaluator flags), and tries scoped `overflow-wrap:anywhere` CSS inside
    a max-width media query. Options, in order: `<wbr>` inserted after `@`, `.`,
    `/` and `://` in the long unbroken tokens (email, URL, 16+ characters) of
    the cells holding them, so breaks fall at natural boundaries (kept only if
    the overflow is gone at every mobile width); the same
    `<wbr>` plus `overflow-wrap:anywhere` on those cells; `overflow-wrap:anywhere`
    alone on those cells; then on every cell of the table. An option is kept
    only if re-rendering shows the table's overflow strictly smaller where it
    overflowed and no larger anywhere, the page scroll width no larger, and no
    more cells squeezed to a few characters per line. Returns the input
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
                for selector, found in tables.items():
                    before = _measure(page, current, selector, load)
                    if before is None:
                        continue
                    for label, css, breaks in _options(selector, found["cells"]):
                        trial = _insert_breaks(current, found["table"], found["indexes"]) if breaks else current
                        if breaks and trial == current:
                            continue
                        if css:
                            trial = _inject_style(trial, f"@media (max-width:{_MOBILE_MAX}px){{{css}}}", "table-wrap")
                        after = _measure(page, trial, selector, load)
                        if _verified(before, after) and (css or all(snapshot["overflow"] <= 1 for snapshot in after.values())):
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
