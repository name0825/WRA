import logging
import re
from pathlib import Path

from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_VIEWPORTS = (390, 768, 1280)

# Finds every computed `position: fixed` element whose nearest ancestor that
# traps fixed positioning does so via backdrop-filter (the only trap this
# module repairs -- filter/transform/etc. are left alone). Skips an ancestor
# whose selector is not unique on the page, or whose ::before is already in
# use, since the repair needs an unused ::before and a stable selector.
_FIND_TRAPS_SCRIPT = """() => {
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${CSS.escape(el.id)}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).map(c => CSS.escape(c)).join('.')}`;
    return tag;
  }
  function backdropOf(style) {
    const backdrop = style.backdropFilter || style.webkitBackdropFilter;
    return backdrop && backdrop !== 'none' ? backdrop : null;
  }
  function findTrappingAncestor(el) {
    let node = el.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      const backdrop = backdropOf(getComputedStyle(node));
      if (backdrop) return { node, backdrop, position: getComputedStyle(node).position };
      node = node.parentElement;
    }
    return null;
  }
  const results = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('*')) {
    if (getComputedStyle(el).position !== 'fixed') continue;
    const trap = findTrappingAncestor(el);
    if (!trap || seen.has(trap.node)) continue;
    seen.add(trap.node);
    const selector = selectorFor(trap.node);
    if (document.querySelectorAll(selector).length !== 1) continue;
    const beforeContent = getComputedStyle(trap.node, '::before').content;
    if (beforeContent && beforeContent !== 'none' && beforeContent !== 'normal') continue;
    results.push({ selector, backdrop: trap.backdrop, position: trap.position });
  }
  return results;
}"""


def _find_traps(page, html: str, load) -> list[dict]:
    traps: dict[str, dict] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        for trap in page.evaluate(_FIND_TRAPS_SCRIPT):
            traps.setdefault(trap["selector"], trap)
    return list(traps.values())


def _still_trapped(page, html: str, selectors: set[str], load) -> bool:
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        for trap in page.evaluate(_FIND_TRAPS_SCRIPT):
            if trap["selector"] in selectors:
                return True
    return False


def _style_block(traps: list[dict]) -> str:
    rules: list[str] = []
    for trap in traps:
        selector = trap["selector"]
        backdrop = trap["backdrop"]
        static_position = "\n  position: relative;" if trap["position"] == "static" else ""
        rules.append(
            f"{selector} {{\n"
            f"  backdrop-filter: none !important;\n"
            f"  -webkit-backdrop-filter: none !important;\n"
            f"  isolation: isolate;{static_position}\n"
            f"}}\n"
            f"{selector}::before {{\n"
            f"  content: \"\";\n"
            f"  position: absolute;\n"
            f"  inset: 0;\n"
            f"  z-index: -1;\n"
            f"  pointer-events: none;\n"
            f"  border-radius: inherit;\n"
            f"  backdrop-filter: {backdrop};\n"
            f"  -webkit-backdrop-filter: {backdrop};\n"
            f"}}"
        )
    return "\n".join(rules)


def _inject_style(html: str, style_content: str, repair: str = "fixed-trap") -> str:
    style_block = f'<style data-wra-repair="{repair}">\n{style_content}\n</style>'
    match = re.search(r"</head>", html, flags=re.IGNORECASE)
    if match:
        return f"{html[:match.start()]}{style_block}\n{html[match.start():]}"
    match = re.search(r"<body[^>]*>", html, flags=re.IGNORECASE)
    if match:
        insert_at = match.end()
        return f"{html[:insert_at]}\n{style_block}{html[insert_at:]}"
    return f"{style_block}\n{html}"


def repair_trapped_fixed(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Move backdrop-filter off headers that trap fixed off-canvas menus.

    A `position: fixed` menu inside an ancestor with `backdrop-filter` is
    positioned relative to that ancestor instead of the viewport, which can
    widen the page. The deterministic fix moves the blur onto the ancestor's
    `::before` (an unused pseudo-element) so the ancestor stops being a
    containing block for `position: fixed` descendants. Repairs are applied
    only if re-rendering confirms the trap is gone; otherwise the input is
    returned unchanged. Skips gracefully (returns the input unchanged) if
    Playwright is unavailable or fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping fixed-trap repair")
        return html, []

    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                traps = _find_traps(page, html, load)
                if not traps:
                    return html, []
                repaired = _inject_style(html, _style_block(traps))
                selectors = {trap["selector"] for trap in traps}
                if _still_trapped(page, repaired, selectors, load):
                    logger.warning("Fixed-trap repair did not clear the trap, leaving HTML as-is")
                    return html, []
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping fixed-trap repair", exc_info=True)
        return html, []

    repairs = [f"moved backdrop-filter off {trap['selector']} onto its ::before to stop trapping a fixed menu" for trap in traps]
    return repaired, repairs
