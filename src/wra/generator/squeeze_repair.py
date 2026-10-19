import logging
from pathlib import Path

from wra.browser.evaluator import CHAR_WRAP_SCRIPT

from .fixed_trap_repair import _VIEWPORTS, _inject_style
from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

# Resolves each CHAR_WRAP_SCRIPT flag back to its element and, for elements
# squeezed by a nowrap flex row (e.g. a nowrap badge eating a header's width
# so a heading shrinks to its min-content and wraps one syllable per line),
# walks from the element up to the nearest ancestor-or-self that is a flex
# item of a row-direction, non-wrapping flex container. Elements whose wrap
# is not caused by such a container are left for the caller to skip. An element
# with no unique selector falls back to a selector scoped to its container.
_FIND_CONTAINER_SCRIPT = """(flags) => {
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${CSS.escape(el.id)}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).map(c => CSS.escape(c)).join('.')}`;
    return tag;
  }
  function indexedSelectorFor(el) {
    const base = selectorFor(el);
    if (el.id || !el.parentElement) return base;
    const same = Array.from(el.parentElement.children).filter(c => c.tagName === el.tagName);
    return same.length > 1 ? `${base}:nth-of-type(${same.indexOf(el) + 1})` : base;
  }
  function uniqueSelectorWith(el, describe) {
    let selector = describe(el);
    let node = el.parentElement;
    for (let depth = 0; depth < 4; depth++) {
      if (document.querySelectorAll(selector).length === 1) return selector;
      if (!node || node === document.documentElement) return null;
      selector = `${describe(node)} > ${selector}`;
      node = node.parentElement;
    }
    return null;
  }
  function uniqueSelector(el) {
    return uniqueSelectorWith(el, selectorFor) || uniqueSelectorWith(el, indexedSelectorFor);
  }
  function isRowNowrap(style) {
    return (style.display === 'flex' || style.display === 'inline-flex') && style.flexWrap === 'nowrap' && /^row/.test(style.flexDirection);
  }
  function findFlexItem(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const parent = node.parentElement;
      if (parent && isRowNowrap(getComputedStyle(parent))) return parent;
    }
    return null;
  }
  function findGridItem(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const parent = node.parentElement;
      if (!parent) return null;
      const style = getComputedStyle(parent);
      if (style.display !== 'grid' && style.display !== 'inline-grid') continue;
      const tracks = style.gridTemplateColumns.split(' ').filter(Boolean).length;
      if (tracks < 2) return null;
      const nodeStyle = getComputedStyle(node);
      if (nodeStyle.gridColumnStart !== 'auto' || nodeStyle.gridColumnEnd !== 'auto') return null;
      const widest = Math.max(...style.gridTemplateColumns.split(' ').map(parseFloat).filter(n => !isNaN(n)));
      if (node.getBoundingClientRect().width > widest + 1) return null;
      const flowChildren = Array.from(parent.children).filter(c => {
        const childStyle = getComputedStyle(c);
        return childStyle.display !== 'none' && childStyle.position !== 'absolute' && childStyle.position !== 'fixed';
      });
      const placed = flowChildren.some(c => {
        const childStyle = getComputedStyle(c);
        return childStyle.gridColumnStart !== 'auto' || childStyle.gridColumnEnd !== 'auto';
      });
      return { element: parent, item: node, tracks, items: flowChildren.length, placed };
    }
    return null;
  }
  const results = [];
  for (const flag of flags) {
    const candidates = Array.from(document.querySelectorAll(flag.selector));
    const el = candidates.find(c => c.textContent.trim() === flag.text || c.textContent.trim().startsWith(flag.text)) || candidates[0];
    if (!el) continue;
    const container = findFlexItem(el);
    if (!container) {
      const grid = findGridItem(el);
      if (!grid) continue;
      const gridSelector = uniqueSelector(grid.element);
      if (!gridSelector) continue;
      const itemSelector = uniqueSelector(grid.item);
      if (grid.placed && !itemSelector) continue;
      const gridTarget = uniqueSelector(el) || `${gridSelector} ${selectorFor(el)}`;
      results.push({ target: gridTarget, container: gridSelector, hangul: /[\\uAC00-\\uD7A3]/.test(el.textContent), grid: { tracks: grid.tracks, items: grid.items, placed: grid.placed, item: itemSelector } });
      continue;
    }
    const containerSelector = uniqueSelector(container);
    if (!containerSelector) continue;
    const target = uniqueSelector(el) || `${containerSelector} ${selectorFor(el)}`;
    results.push({ target, container: containerSelector, hangul: /[\\uAC00-\\uD7A3]/.test(el.textContent) });
  }
  return results;
}"""

# Re-measures a specific element's own text against the same char-count/
# line-count thresholds CHAR_WRAP_SCRIPT uses, plus the document's scroll
# width, so a repair is kept only if it actually stops the wrap without
# introducing new horizontal overflow.
_VERIFY_SCRIPT = """(targets) => {
  const viewport = window.innerWidth;
  const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0);
  const wrapped = [];
  for (const target of targets) {
    scan: for (const el of document.querySelectorAll(target)) {
      for (const node of el.childNodes) {
        if (node.nodeType !== Node.TEXT_NODE) continue;
        const text = node.textContent.trim();
        if (text.length < 3) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const lineCount = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0).length;
        if (lineCount < 2) continue;
        if (lineCount <= text.split(/\\s+/).length) continue;
        const charCount = text.replace(/\\s+/g, '').length;
        if ((charCount >= 8 && charCount / lineCount <= 4) || (charCount >= 3 && lineCount >= 3 && charCount / lineCount <= 1.5)) {
          wrapped.push(target);
          break scan;
        }
      }
    }
  }
  return { viewport, scrollWidth, wrapped };
}"""

# Reports which targets have a rendered element above the viewport top or
# below the bottom of an ancestor that clips its overflow.
_CLIP_SCRIPT = """(targets) => {
  const clipped = [];
  for (const target of targets) {
    scan: for (const el of document.querySelectorAll(target)) {
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const own = getComputedStyle(el);
      if (own.visibility === 'hidden' || Number(own.opacity) === 0) continue;
      if (rect.top < 0) { clipped.push(target); break scan; }
      for (let anc = el.parentElement; anc && anc !== document.body && anc !== document.documentElement; anc = anc.parentElement) {
        const overflowY = getComputedStyle(anc).overflowY;
        if (overflowY !== 'hidden' && overflowY !== 'clip') continue;
        if (rect.bottom > anc.getBoundingClientRect().bottom + 1) { clipped.push(target); break scan; }
        break;
      }
    }
  }
  return clipped;
}"""

# Lists every ancestor (below body) of each squeezed container that has a
# unique selector, with its height, position, whether its box vertically
# contains the container, and whether its height is fixed (changes when set
# to auto).
_UNCLIP_SCRIPT = """(containers) => {
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${CSS.escape(el.id)}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).map(c => CSS.escape(c)).join('.')}`;
    return tag;
  }
  function indexedSelectorFor(el) {
    const base = selectorFor(el);
    if (el.id || !el.parentElement) return base;
    const same = Array.from(el.parentElement.children).filter(c => c.tagName === el.tagName);
    return same.length > 1 ? `${base}:nth-of-type(${same.indexOf(el) + 1})` : base;
  }
  function uniqueSelectorWith(el, describe) {
    let selector = describe(el);
    let node = el.parentElement;
    for (let depth = 0; depth < 4; depth++) {
      if (document.querySelectorAll(selector).length === 1) return selector;
      if (!node || node === document.documentElement) return null;
      selector = `${describe(node)} > ${selector}`;
      node = node.parentElement;
    }
    return null;
  }
  function uniqueSelector(el) {
    return uniqueSelectorWith(el, selectorFor) || uniqueSelectorWith(el, indexedSelectorFor);
  }
  const results = [];
  for (const containerSelector of containers) {
    const container = document.querySelector(containerSelector);
    if (!container) continue;
    const rect = container.getBoundingClientRect();
    for (let anc = container.parentElement; anc && anc !== document.body; anc = anc.parentElement) {
      const ancRect = anc.getBoundingClientRect();
      const selector = uniqueSelector(anc);
      if (!selector) continue;
      const contains = rect.top >= ancRect.top - 1 && rect.bottom <= ancRect.bottom + 1;
      let fixed = false;
      if (!contains) {
        const inline = anc.style.height;
        anc.style.height = 'auto';
        fixed = Math.abs(anc.getBoundingClientRect().height - ancRect.height) > 1;
        anc.style.height = inline;
      }
      results.push({ container: containerSelector, selector, height: ancRect.height, position: getComputedStyle(anc).position, contains, fixed });
    }
  }
  return results;
}"""

# Reports whether any container in the given container/ancestor selector
# pairs still extends vertically outside its ancestor.
_OUTSIDE_SCRIPT = """(pairs) => pairs.some(([container, ancestor]) => {
  const c = document.querySelector(container);
  const a = document.querySelector(ancestor);
  if (!c || !a) return false;
  const rect = c.getBoundingClientRect();
  const ancRect = a.getBoundingClientRect();
  return rect.top < ancRect.top - 1 || rect.bottom > ancRect.bottom + 1;
});"""


def _find_squeezed(page, html: str, load) -> tuple[list[dict], dict[int, int]]:
    found: dict[tuple[str, str], dict] = {}
    baseline_scroll: dict[int, int] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        baseline_scroll[width] = page.evaluate("() => Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0)")
        flags = page.evaluate(CHAR_WRAP_SCRIPT)
        if not flags:
            continue
        for item in page.evaluate(_FIND_CONTAINER_SCRIPT, flags):
            key = (item["target"], item["container"])
            entry = found.setdefault(key, {**item, "widths": []})
            if width not in entry["widths"]:
                entry["widths"].append(width)
            if "grid" in item:
                entry.setdefault("grid_by_width", {})[width] = item["grid"]
    return list(found.values()), baseline_scroll


def _baseline_clipped(page, html: str, targets: list[str], load) -> dict[int, set[str]]:
    clipped: dict[int, set[str]] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        clipped[width] = set(page.evaluate(_CLIP_SCRIPT, targets))
    return clipped


def _still_broken(
    page,
    html: str,
    targets: list[str],
    baseline_scroll: dict[int, int],
    baseline_clipped: dict[int, set[str]],
    ancestors: list[dict],
    load,
) -> bool:
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        result = page.evaluate(_VERIFY_SCRIPT, targets)
        if result["wrapped"]:
            return True
        pairs = [pair for ancestor in ancestors for pair in ancestor["pairs"].get(width, [])]
        if pairs and page.evaluate(_OUTSIDE_SCRIPT, pairs):
            return True
        if set(page.evaluate(_CLIP_SCRIPT, targets)) - baseline_clipped[width]:
            return True
        if baseline_scroll[width] <= result["viewport"] and result["scrollWidth"] > result["viewport"]:
            return True
    return False


def _find_clipping_ancestors(page, repaired: str, squeezed: list[dict], load) -> list[dict]:
    found: dict[str, dict] = {}
    for width in sorted({w for item in squeezed for w in item["widths"]}):
        page.set_viewport_size({"width": width, "height": 800})
        containers = [item["container"] for item in squeezed if width in item["widths"]]
        load(page, repaired)
        for entry in page.evaluate(_UNCLIP_SCRIPT, containers):
            if entry["contains"] or not entry["fixed"] or entry["height"] <= 0:
                continue
            if entry["position"] not in ("static", "relative", "sticky"):
                continue
            ancestor = found.setdefault(entry["selector"], {"selector": entry["selector"], "bands": {}, "pairs": {}})
            ancestor["bands"][width] = entry["height"]
            ancestor["pairs"].setdefault(width, []).append([entry["container"], entry["selector"]])
    return list(found.values())


_TRACKS_SCRIPT = """(selector) => {
  const el = document.querySelector(selector);
  return el ? getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length : 0;
}"""


def _find_grid_lower_bounds(page, html: str, squeezed: list[dict], load) -> None:
    viewports = sorted(_VIEWPORTS)
    found: dict[tuple[str, int], int] = {}
    for item in squeezed:
        for width, grid in item.get("grid_by_width", {}).items():
            key = (item["container"], width)
            if key not in found:
                low = max((v for v in viewports if v < width), default=0) + 1
                high = width
                page.set_viewport_size({"width": width, "height": 800})
                load(page, html)
                while low < high:
                    middle = (low + high) // 2
                    page.set_viewport_size({"width": middle, "height": 800})
                    if page.evaluate(_TRACKS_SCRIPT, item["container"]) == grid["tracks"]:
                        high = middle
                    else:
                        low = middle + 1
                found[key] = low
            grid["min_width"] = found[key]


def _grid_plan(squeezed: list[dict], option: str) -> dict[tuple[str, int], tuple[str, int, int]]:
    plan: dict[tuple[str, int], tuple[str, int, int]] = {}
    for item in squeezed:
        for width, grid in item.get("grid_by_width", {}).items():
            if option == "columns" and grid["items"] < grid["tracks"] and not grid["placed"]:
                plan[(item["container"], width)] = ("gridcols", grid["items"], grid["min_width"])
            else:
                selector = grid["item"] if grid["placed"] else f"{item['container']} > *"
                plan[(selector, width)] = ("gridspan", 0, grid["min_width"])
    return plan


def _grid_query(width: int, min_width: int) -> str:
    viewports = sorted(_VIEWPORTS)
    parts = []
    if min_width > 1:
        parts.append(f"(min-width: {min_width}px)")
    if width < viewports[-1]:
        parts.append(f"(max-width: {width}px)")
    return " and ".join(parts)


def _style_block(squeezed: list[dict], ancestors: list[dict] | None = None, grid_option: str = "columns") -> str:
    rules: list[str] = []
    plan = _grid_plan(squeezed, grid_option)
    for selector, width in sorted(plan):
        kind, count, min_width = plan[(selector, width)]
        if kind == "gridcols":
            declaration = f"{selector} {{\n    grid-template-columns: repeat({count}, minmax(0, 1fr)) !important;\n  }}"
        else:
            declaration = f"{selector} {{\n    grid-column: 1 / -1 !important;\n  }}"
        rules.append(f"@media {_grid_query(width, min_width)} {{\n  {declaration}\n}}")
    for item in squeezed:
        if "grid" in item:
            continue
        # Unconditional, not media-query-scoped: unlike the overflow repairs'
        # navwrap fix, flex-wrap: wrap is inert at widths where the row
        # already fits (wrapping only happens once items stop fitting), so
        # there is no wider viewport this could regress.
        rules.append(f"{item['container']} {{\n  flex-wrap: wrap !important;\n  row-gap: 0.5rem !important;\n}}")
        if item["hangul"]:
            rules.append(f"{item['target']} {{\n  word-break: keep-all !important;\n}}")
    viewports = sorted(_VIEWPORTS)
    for ancestor in ancestors or []:
        for width, height in sorted(ancestor["bands"].items()):
            smaller = [v for v in viewports if v < width]
            query = f"(min-width: {smaller[-1] + 1}px) and (max-width: {width}px)" if smaller else f"(max-width: {width}px)"
            rules.append(
                f"@media {query} {{\n  {ancestor['selector']} {{\n"
                f"    height: auto !important;\n    min-height: {height:g}px !important;\n  }}\n}}"
            )
    return "\n".join(rules)


def _verified_repair(
    page,
    html: str,
    squeezed: list[dict],
    baseline_scroll: dict[int, int],
    baseline_clipped: dict[int, set[str]],
    load,
) -> tuple[str, list[dict], str] | None:
    targets = [item["target"] for item in squeezed]
    grid_options = ("columns", "span") if any("grid" in item for item in squeezed) else ("columns",)
    for grid_option in grid_options:
        repaired = _inject_style(html, _style_block(squeezed, grid_option=grid_option), "squeeze")
        ancestors = _find_clipping_ancestors(page, repaired, squeezed, load)
        if ancestors:
            repaired = _inject_style(html, _style_block(squeezed, ancestors, grid_option), "squeeze")
        if not _still_broken(page, repaired, targets, baseline_scroll, baseline_clipped, ancestors, load):
            return repaired, ancestors, grid_option
    return None


def repair_squeezed_text(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Fix headings squeezed to one-syllable-per-line by a nowrap flex sibling.

    A row-direction flex container with `flex-wrap: nowrap` that also holds a
    `white-space: nowrap` sibling (e.g. a badge or label) forces the other
    flex items to shrink to their min-content width once the row runs out of
    space, which for Hangul text collapses a heading to a single syllable
    per line. The deterministic fix lets the container wrap and, for Hangul
    text, adds `word-break: keep-all` so wrapping happens between words
    instead of mid-syllable-block. Repairs are applied only if re-rendering
    confirms the text no longer wraps this way and no viewport gains new
    horizontal overflow; otherwise the input is returned unchanged. Skips
    gracefully (returns the input unchanged) if Playwright is unavailable or
    fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping squeeze repair")
        return html, []

    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                squeezed, baseline_scroll = _find_squeezed(page, html, load)
                if not squeezed:
                    return html, []
                baseline_clipped = _baseline_clipped(page, html, [item["target"] for item in squeezed], load)
                if any("grid" in item for item in squeezed):
                    _find_grid_lower_bounds(page, html, squeezed, load)
                result = _verified_repair(page, html, squeezed, baseline_scroll, baseline_clipped, load)
                if result is None and any("grid" in item for item in squeezed):
                    squeezed = [item for item in squeezed if "grid" not in item]
                    result = _verified_repair(page, html, squeezed, baseline_scroll, baseline_clipped, load) if squeezed else None
                if result is None:
                    logger.warning("Squeeze repair did not clear the wrap, leaving HTML as-is")
                    return html, []
                repaired, ancestors, grid_option = result
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping squeeze repair", exc_info=True)
        return html, []

    repairs: list[str] = []
    for (selector, width), (kind, _, _) in sorted(_grid_plan(squeezed, grid_option).items()):
        repairs.append(f"{kind}: {selector} @{width}px")
    for item in squeezed:
        if "grid" in item:
            continue
        repairs.extend(f"flexwrap: {item['container']} @{width}px" for width in item["widths"])
        if item["hangul"]:
            repairs.extend(f"keepall: {item['target']} @{width}px" for width in item["widths"])
    for ancestor in ancestors:
        repairs.extend(f"unclip: {ancestor['selector']} @{width}px" for width in sorted(ancestor["bands"]))
    return repaired, repairs
