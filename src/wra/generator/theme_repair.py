import logging
from pathlib import Path

from wra.browser.contrast import (
    CONTRAST_CANDIDATES_SCRIPT,
    NORMAL_TEXT_MIN_RATIO,
    RGB_HELPER_JS,
    contrast_failures,
    contrast_ratio,
    parse_css_color,
)
from wra.browser.reveal import reveal_animated_content

from .fixed_trap_repair import _inject_style
from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_WIDTHS = (1280, 390)
_REVEAL_TIMEOUT_MS = 10000
_LOAD_TIMEOUT_MS = 10000
_ROOT_MIN_RATIO = NORMAL_TEXT_MIN_RATIO
_BODY_MAX_RATIO = 3.0

# Reads the root and body colours and whether the author (an inline <style>
# sheet, the body's style attribute or a bg-* class, not a linked framework
# sheet) styles the body background or colour.
_DETECT_SCRIPT = """() => {
""" + RGB_HELPER_JS + """
  const body = document.body;
  const backgroundProps = ['background', 'background-color', 'background-image'];
  let background = Array.from(body.classList).some(name => /(^|:)bg-/.test(name)) || backgroundProps.some(name => body.style.getPropertyValue(name));
  let color = !!body.style.getPropertyValue('color');
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule.media && rule.media.mediaText.trim().toLowerCase() === 'print') continue;
      if (rule.selectorText && rule.style) {
        try {
          if (body.matches(rule.selectorText)) {
            background = background || backgroundProps.some(name => rule.style.getPropertyValue(name));
            color = color || !!rule.style.getPropertyValue('color');
          }
        } catch (e) {}
      }
      if (rule.cssRules) walk(rule.cssRules);
    }
  };
  for (const sheet of document.styleSheets) {
    const owner = sheet.ownerNode;
    if (!owner || owner.tagName !== 'STYLE' || owner.hasAttribute('data-wra-repair')) continue;
    try { walk(sheet.cssRules); } catch (e) {}
  }
  const root = getComputedStyle(document.documentElement), main = getComputedStyle(body);
  return {
    background, color,
    rootColor: rgb(root.color), rootBackground: rgb(root.backgroundColor), rootImage: root.backgroundImage,
    bodyColor: rgb(main.color), bodyBackground: rgb(main.backgroundColor), bodyImage: main.backgroundImage,
  };
}"""


def _opaque(value: str) -> tuple[float, float, float] | None:
    parsed = parse_css_color(value)
    return parsed[:3] if parsed and parsed[3] == 1.0 else None


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(value):02x}" for value in rgb)


def _overridden(info: dict) -> tuple[str, str, str, str] | None:
    if info["background"] or info["rootImage"] != "none" or info["bodyImage"] != "none":
        return None
    root_color, body_color = parse_css_color(info["rootColor"]), parse_css_color(info["bodyColor"])
    root_background, body_background = _opaque(info["rootBackground"]), _opaque(info["bodyBackground"])
    if not (root_color and body_color and root_background and body_background):
        return None
    if contrast_ratio(root_color[:3], root_background) < _ROOT_MIN_RATIO or contrast_ratio(root_color[:3], body_background) >= _BODY_MAX_RATIO:
        return None
    return _hex(root_background), _hex(root_color[:3]), _hex(body_background), _hex(body_color[:3])


def _count_failures(page, html: str, load) -> list[int]:
    page.set_viewport_size({"width": _WIDTHS[0], "height": 800})
    load(page, html, timeout=_LOAD_TIMEOUT_MS)
    reveal_animated_content(page, _REVEAL_TIMEOUT_MS)
    counts = []
    for width in _WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        counts.append(sum(1 for candidate in page.evaluate(CONTRAST_CANDIDATES_SCRIPT) if contrast_failures([candidate])))
    return counts


def repair_overridden_root_theme(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Let the root theme show through a body painted by a framework default.

    A page that sets its dark theme on `html` and styles no body background is
    covered by a linked framework's `body` defaults (an opaque white
    background and a reset text colour), which hides light text or leaves dark
    text on dark sections. When the root colour is readable on the root
    background but not on the body background, and no author rule, inline
    style or `bg-*` class styles the body background, one
    `<style data-wra-repair="theme">` block makes the body transparent and
    lets it inherit the root colour (unless the author sets a body colour).
    A re-render at desktop and mobile widths verifies that no width gains
    low-contrast text and the total drops; otherwise the input is returned
    unchanged. Skips gracefully (returns the input unchanged) if Playwright is
    unavailable or fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping theme repair")
        return html, []

    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                page.set_viewport_size({"width": _WIDTHS[0], "height": 800})
                load(page, html, timeout=_LOAD_TIMEOUT_MS)
                info = page.evaluate(_DETECT_SCRIPT)
                colors = _overridden(info)
                if colors is None:
                    return html, []
                declarations = "background-color:transparent" if info["color"] else "background-color:transparent;color:inherit"
                repaired = _inject_style(html, f"body{{{declarations}}}", "theme")
                before = _count_failures(page, html, load)
                after = _count_failures(page, repaired, load)
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping theme repair", exc_info=True)
        return html, []

    if any(new > old for new, old in zip(after, before)) or sum(after) >= sum(before):
        logger.warning("Theme repair did not verify (low-contrast %s->%s), leaving HTML as-is", before, after)
        return html, []
    return repaired, [f"html {colors[0]}/{colors[1]} under body {colors[2]}/{colors[3]}, low-contrast {sum(before)}->{sum(after)}"]
