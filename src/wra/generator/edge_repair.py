import logging
from pathlib import Path

from wra.browser.evaluator import EDGE_HUGGING_HITS_FUNCTION

from .fixed_trap_repair import _VIEWPORTS, _inject_style
from .overflow_repair import _scoped
from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_FALLBACK_PADDING = 16
_MAX_ROUNDS = 3
_MAX_PER_ROUND = 8

# Finds the text runs the evaluator's edge-hugging check reports, then for each
# one picks the box whose missing side padding put the text on the edge: a
# full-viewport-width ancestor (the text's own element included) with under 4px
# padding on the touching side. Among those it prefers the nearest
# section-like landmark, then the nearest one that shares a class with an
# element that does have a side padding of 8-64px, then the nearest one. The
# padding to restore, on each side of it that has under 4px, is that sharing
# element's (the padding the author meant to give it, wiped by a later or more
# specific rule), or 16px when none shows one.
_FIND_SCRIPT = """() => {
  %s
  const vw = document.documentElement.clientWidth;
  const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0);
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
  function siblingPadding(node) {
    for (const name of node.classList) {
      for (const other of document.getElementsByClassName(name)) {
        if (other === node) continue;
        const padding = parseFloat(getComputedStyle(other).paddingLeft) || 0;
        if (padding >= 8 && padding <= 64) return padding;
      }
    }
    return null;
  }
  const keys = [];
  const targets = [];
  for (const hit of edgeHuggingHits()) {
    keys.push(hit.selector + '|' + hit.text);
    const touchLeft = hit.left <= 4;
    const touchRight = hit.right >= vw - 4;
    const chain = [];
    for (let node = hit.element; node && node !== document.body && node !== document.documentElement; node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      if (rect.left > 1 || rect.right < vw - 1) continue;
      const style = getComputedStyle(node);
      const padLeft = parseFloat(style.paddingLeft) || 0;
      const padRight = parseFloat(style.paddingRight) || 0;
      if (!((touchLeft && padLeft < 4) || (touchRight && padRight < 4))) continue;
      chain.push({ node, padLeft, padRight, sibling: siblingPadding(node) });
    }
    const picked = chain.find(c => /^(SECTION|HEADER|FOOTER|NAV|ARTICLE|ASIDE)$/.test(c.node.tagName))
      || chain.find(c => c.sibling !== null)
      || chain[0];
    if (!picked) continue;
    const target = uniqueSelector(picked.node);
    if (!target) continue;
    const padding = picked.sibling ?? %d;
    targets.push({ target, key: hit.selector + '|' + hit.text, left: picked.padLeft < 4 ? padding : 0, right: picked.padRight < 4 ? padding : 0 });
  }
  return { viewport: vw, scrollWidth, keys, targets };
}""" % (EDGE_HUGGING_HITS_FUNCTION.replace("%", "%%"), _FALLBACK_PADDING)


def _measure(page, html: str, load) -> dict[int, dict]:
    snapshots: dict[int, dict] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        snapshots[width] = page.evaluate(_FIND_SCRIPT)
    return snapshots


def _collect(snapshots: dict[int, dict]) -> list[dict]:
    candidates: dict[str, dict] = {}
    for width, snapshot in snapshots.items():
        for found in snapshot["targets"]:
            candidate = candidates.setdefault(found["target"], {"target": found["target"], "sides": {}, "keys": {}})
            left, right = candidate["sides"].get(width, (0, 0))
            candidate["sides"][width] = (max(left, found["left"]), max(right, found["right"]))
            candidate["keys"].setdefault(width, []).append(found["key"])
    return list(candidates.values())


def _rule(candidate: dict) -> str:
    rules = []
    for width in sorted(candidate["sides"], reverse=True):
        left, right = candidate["sides"][width]
        declarations = " ".join(
            f"padding-{side}: {value:g}px !important;" for side, value in (("left", left), ("right", right)) if value
        )
        rules.append(_scoped(f"{candidate['target']} {{ {declarations} }}", [width]))
    return "\n".join(rules)


def _label(candidate: dict) -> str:
    groups: dict[str, list[str]] = {}
    for width in sorted(candidate["sides"]):
        left, right = candidate["sides"][width]
        token = f"{left:g}px" if left == right else f"{left:g}px/{right:g}px"
        groups.setdefault(token, []).append(str(width))
    return f"{candidate['target']}@" + " ".join(f"{','.join(widths)} {token}" for token, widths in groups.items())


def _verified(baseline: dict[int, dict], trial: dict[int, dict], candidate: dict) -> bool:
    for width in _VIEWPORTS:
        before, after = baseline[width], trial[width]
        if after["scrollWidth"] > max(before["scrollWidth"], after["viewport"]):
            return False
        if any(key in after["keys"] for key in candidate["keys"].get(width, [])):
            return False
    return True


def repair_edge_hugging_text(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Restore side padding on full-width boxes whose text touches the viewport edge.

    Renders the page at each viewport width, finds text runs whose glyphs sit
    on the left or right edge (the evaluator's edge-hugging check), and gives
    the full-width box behind each one, on each side that has none, the side
    padding a sibling sharing its class already has (16px when none does),
    scoped per width to the widths where the hit occurred. Repeats on the
    repaired page, since the check reports at most 20 runs and later sections
    stay hidden until earlier ones are fixed. A fix is kept only if
    re-rendering shows its text off the edge and no viewport gets wider. If
    rendering fails midway, the fixes verified so far are kept. Returns the
    input unchanged if nothing verified or Playwright is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping edge repair")
        return html, []

    accepted: list[str] = []
    repairs: list[str] = []
    tried = False
    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                current = html
                done: set[str] = set()
                for _ in range(_MAX_ROUNDS):
                    baseline = _measure(page, current, load)
                    candidates = [c for c in _collect(baseline) if c["target"] not in done][:_MAX_PER_ROUND]
                    round_accepted = 0
                    for candidate in candidates:
                        tried = True
                        done.add(candidate["target"])
                        css = _rule(candidate)
                        trial = _measure(page, _inject_style(html, "\n".join([*accepted, css]), "edge"), load)
                        if _verified(baseline, trial, candidate):
                            accepted.append(css)
                            round_accepted += 1
                            repairs.append(_label(candidate))
                    if not round_accepted:
                        break
                    current = _inject_style(html, "\n".join(accepted), "edge")
            finally:
                browser.close()
    except Exception:
        logger.warning("Edge repair failed after %d verified fixes, keeping those", len(accepted), exc_info=True)

    if not accepted:
        if tried:
            logger.warning("Edge repair found candidates but none verified, leaving HTML as-is")
        return html, []
    return _inject_style(html, "\n".join(accepted), "edge"), repairs
