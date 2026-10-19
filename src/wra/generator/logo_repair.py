import logging
from pathlib import Path

from wra.browser.evaluator import OVERSIZED_LOGO_FUNCTION

from .fixed_trap_repair import _VIEWPORTS, _inject_style
from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_LOGO_HEIGHT = 40
_MAX_CANDIDATES = 5

# Finds the oversized header logos the evaluator's header-logo check reports
# and gives each a selector that matches only it on the page (its tag with
# id/classes, prefixed by up to four ancestors, with an :nth-of-type fallback
# for id-less, class-less siblings). A logo without one is skipped.
_FIND_SCRIPT = """() => {
  %s
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
  const logos = [];
  for (const logo of oversizedLogos()) {
    const selector = logo.inContext ? uniqueSelector(logo.el) : null;
    if (selector) logos.push({ selector, width: logo.width, height: logo.height });
  }
  return { viewport: document.documentElement.clientWidth, scrollWidth, logos };
}""" % OVERSIZED_LOGO_FUNCTION


def _measure(page, html: str, load) -> dict[int, dict]:
    snapshots: dict[int, dict] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        snapshots[width] = page.evaluate(_FIND_SCRIPT)
    return snapshots


def _collect(snapshots: dict[int, dict]) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for snapshot in snapshots.values():
        for logo in snapshot["logos"]:
            if logo["selector"] not in candidates or logo["height"] > candidates[logo["selector"]]["height"]:
                candidates[logo["selector"]] = logo
    return candidates


def _verified(baseline: dict[int, dict], trial: dict[int, dict], selector: str) -> bool:
    for width in _VIEWPORTS:
        if any(logo["selector"] == selector for logo in trial[width]["logos"]):
            return False
        if trial[width]["scrollWidth"] > max(baseline[width]["scrollWidth"], trial[width]["viewport"]):
            return False
    return True


def repair_oversized_header_logo(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Cap the height of header logos that render far larger than a logo.

    Renders the page at each viewport width, finds the img/svg the evaluator's
    header-logo check reports (in a nav, a home link or a logo/brand-named
    element, an image half the header wide or more still counts), and gives
    each a 40px height with automatic width at every width. A fix is kept only
    if re-rendering shows the logo no longer reported at any width and no
    viewport gets wider. Returns the input unchanged if nothing verified or
    Playwright is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping logo repair")
        return html, []

    accepted: list[str] = []
    repairs: list[str] = []
    tried = False
    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                baseline = _measure(page, html, load)
                for selector, logo in list(_collect(baseline).items())[:_MAX_CANDIDATES]:
                    tried = True
                    css = f"{selector} {{ height: {_LOGO_HEIGHT}px !important; width: auto !important; max-width: 100% !important; }}"
                    trial = _measure(page, _inject_style(html, "\n".join([*accepted, css]), "logo"), load)
                    if _verified(baseline, trial, selector):
                        accepted.append(css)
                        repairs.append(f"{selector} {logo['width']}x{logo['height']}->{_LOGO_HEIGHT}px")
            finally:
                browser.close()
    except Exception:
        logger.warning("Logo repair failed after %d verified fixes, keeping those", len(accepted), exc_info=True)

    if not accepted:
        if tried:
            logger.warning("Logo repair found candidates but none verified, leaving HTML as-is")
        return html, []
    return _inject_style(html, "\n".join(accepted), "logo"), repairs
