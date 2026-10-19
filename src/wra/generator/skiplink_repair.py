import logging
import re
from pathlib import Path

from .fixed_trap_repair import _VIEWPORTS, _inject_style
from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_TOLERANCE = 0.5
_BLOCK_RE = re.compile(r'<style data-wra-repair="skip-link">\s*((?:(?!</style>).)*?)\s*</style>\n?', re.DOTALL)

# Finds the skip link that is meant to stay off-screen until focused but whose
# box still protrudes a few px into the viewport at rest: the first focusable
# element of the document, an in-page anchor, absolutely or fixed positioned,
# rendered, not hidden by clip/clip-path, and with the part inside the viewport
# under half its height. The half-height bound keeps a visible anchor that is
# merely shifted up a few px out of it. Skips a link that, or whose ancestor,
# runs an animation that moves its box (it is on its way into view), and a link
# offset only because a positioned ancestor is off-screen. Skips a link that
# has no unique selector.
_FIND_SCRIPT = """() => {
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
  function isFocusable(node) {
    if (node.disabled || node.matches('input[type="hidden"]') || node.tabIndex < 0) return false;
    return node.getClientRects().length > 0 && getComputedStyle(node).visibility !== 'hidden';
  }
  function movesBox(node) {
    const moving = ['transform', 'translate', 'top', 'inset', 'insetBlockStart', 'insetBlock'];
    return node.getAnimations().some(animation => {
      const keyframes = animation.effect && animation.effect.getKeyframes ? animation.effect.getKeyframes() : null;
      return !keyframes || keyframes.some(frame => moving.some(name => name in frame));
    });
  }
  const el = Array.from(document.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex]')).find(isFocusable);
  if (!el || !el.matches('a[href^="#"]')) return null;
  const style = getComputedStyle(el);
  if (style.position !== 'absolute' && style.position !== 'fixed') return null;
  if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return null;
  if (style.clipPath !== 'none' || (style.clip !== 'auto' && style.clip !== '')) return null;
  const rect = el.getBoundingClientRect();
  if (rect.width <= 1 || !(rect.top < 0 && rect.bottom > 0 && rect.bottom < rect.height / 2)) return null;
  for (let node = el; node; node = node.parentElement) {
    if (movesBox(node)) return null;
    if (node === el || node === document.documentElement || node === document.body) continue;
    const position = getComputedStyle(node).position;
    if (['absolute', 'fixed', 'sticky'].includes(position) && node.getBoundingClientRect().top < 0) return null;
  }
  const selector = uniqueSelector(el);
  return selector ? { selector, bottom: rect.bottom } : null;
}"""

# Reads the selector's rect at rest, then its rect while focused (with
# transitions and animations switched off so the focused rect is the settled
# one). A selector that no longer matches one element reads as null.
_MEASURE_SCRIPT = """(selector) => {
  const found = document.querySelectorAll(selector);
  if (found.length !== 1) return { rest: null, focused: null };
  const el = found[0];
  const read = () => {
    const rect = el.getBoundingClientRect();
    return { top: rect.top, left: rect.left, width: rect.width, height: rect.height, bottom: rect.bottom };
  };
  const rest = read();
  const style = document.createElement('style');
  style.textContent = '*, *::before, *::after { transition: none !important; animation: none !important; }';
  document.head.appendChild(style);
  el.focus();
  const focused = read();
  el.blur();
  style.remove();
  return { rest, focused };
}"""


def _find(page, html: str, load) -> dict | None:
    candidate = None
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        found = page.evaluate(_FIND_SCRIPT)
        if found:
            candidate = candidate or found
            candidate["bottom"] = max(candidate["bottom"], found["bottom"])
    return candidate


def _measure(page, html: str, selector: str, load) -> dict[int, dict]:
    snapshots: dict[int, dict] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        snapshots[width] = page.evaluate(_MEASURE_SCRIPT, selector)
    return snapshots


def _same_rect(before: dict, after: dict) -> bool:
    return all(abs(before[key] - after[key]) <= _TOLERANCE for key in ("top", "left", "height"))


def _verified(baseline: dict[int, dict], trial: dict[int, dict]) -> bool:
    for width in _VIEWPORTS:
        rest, focused = trial[width]["rest"], trial[width]["focused"]
        before = baseline[width]["focused"]
        if rest is None or focused is None or before is None:
            return False
        if rest["bottom"] > 0 or not _same_rect(before, focused):
            return False
    return True


def _style_rule(candidate: dict) -> str:
    return f"{candidate['selector']}:not(:focus):not(:focus-within) {{ transform: translateY(-100%) !important; }}"


def repair_protruding_skip_link(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Hide a skip link whose off-screen offset leaves a sliver inside the viewport.

    A skip link parked with a fixed offset such as `top: -40px` is taller than
    that once padding is added, so its bottom edge shows as a thin strip at the
    top of every viewport. The repair translates it fully upward while it is
    not focused, so the page's own focus reveal still applies. A skip-link
    block left by an earlier run is dropped first and recomputed. The repair
    is applied only if re-rendering confirms the link sits above the viewport
    at rest and its focused rect is unchanged; otherwise the input (minus any
    earlier block) is returned. Skips gracefully (returns the input unchanged)
    if Playwright is unavailable or fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping skip-link repair")
        return html, []

    existing = _BLOCK_RE.findall(html)
    stripped = _BLOCK_RE.sub("", html)

    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                candidate = _find(page, stripped, load)
                if not candidate:
                    return stripped, []
                rule = _style_rule(candidate)
                baseline = _measure(page, stripped, candidate["selector"], load)
                repaired = _inject_style(stripped, rule, "skip-link")
                trial = _measure(page, repaired, candidate["selector"], load)
                if not _verified(baseline, trial):
                    logger.warning("Skip-link repair did not verify, leaving HTML as-is")
                    return stripped, []
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping skip-link repair", exc_info=True)
        return html, []

    if existing == [rule]:
        return html, []
    return repaired, [f"{candidate['selector']} +{candidate['bottom']:.1f}px"]
