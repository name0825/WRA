import logging
import re
from collections import Counter
from pathlib import Path

from wra.browser.craft_measure import CRAFT_VIEWPORT, JS_OFF_SCRIPT
from wra.browser.guard import guard_context, launch_guarded
from wra.validator.visual import _JS_OFF_HIDDEN_MIN_CHARS, _JS_OFF_HIDDEN_MIN_RATIO
from .page_render import page_loader

logger = logging.getLogger(__name__)

_RULE = "opacity:1!important;visibility:visible!important;transform:none!important"
_SKIPPED = r"<!--.*?-->|<script\b.*?</script\s*>|<style\b.*?</style\s*>"
_MAX_ROUNDS = 3


def _measure(page) -> dict:
    return page.evaluate(JS_OFF_SCRIPT)


def _tripped(measured: dict) -> bool:
    hidden, total = measured["hidden"], measured["total"]
    return hidden >= _JS_OFF_HIDDEN_MIN_CHARS and hidden / max(total, 1) >= _JS_OFF_HIDDEN_MIN_RATIO


def _selectors(measured: dict) -> list[str]:
    culprits, counts = measured["culprits"], measured["counts"]
    carried = Counter(token for culprit in culprits for token in culprit["classes"])
    eligible = {token for token, number in carried.items() if counts[token] == number}
    uncovered = list(range(len(culprits)))
    selectors: list[str] = []
    while True:
        tally = Counter(token for index in uncovered for token in culprits[index]["classes"] if token in eligible)
        if not tally:
            break
        token = min(tally, key=lambda name: (-tally[name], counts[name], name))
        selectors.append(f".{token}")
        uncovered = [index for index in uncovered if token not in culprits[index]["classes"]]
    for index in uncovered:
        selector = f"#{culprits[index]['id']}" if culprits[index]["id"] else None
        if selector and selector not in selectors:
            selectors.append(selector)
    return selectors


def _find_outside_raw_text(html: str, tag_pattern: str):
    for match in re.finditer(f"{_SKIPPED}|({tag_pattern})", html, flags=re.IGNORECASE | re.DOTALL):
        if match.group(1):
            return match
    return None


def _inject_noscript(html: str, selectors: list[str]) -> str | None:
    block = f"<noscript><style>{','.join(selectors)}{{{_RULE}}}</style></noscript>"
    match = _find_outside_raw_text(html, r"</head\s*>")
    if match:
        return f"{html[:match.start()]}{block}\n{html[match.start():]}"
    match = _find_outside_raw_text(html, r"<body\b[^>]*>")
    if match:
        return f"{html[:match.end()]}\n{block}{html[match.end():]}"
    return None


def repair_js_off_hidden(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Show content that stays at opacity 0 or visibility hidden when JavaScript is off.

    Scroll-reveal classes such as `.reveal` hide their element until a script
    adds a visible class, so with JavaScript disabled most of the text stays
    invisible. The repair finds, with JavaScript disabled, the elements that
    hide the text, picks class tokens (or ids for class-less elements) that
    cover them, and adds a `<noscript><style>` block forcing them visible, so
    the page with JavaScript enabled is unaffected. The repair is applied only
    if re-measuring with JavaScript disabled confirms the hidden text is now
    below the refiner's defect thresholds; otherwise the input is returned
    unchanged. Skips gracefully (returns the input unchanged) if Playwright is
    unavailable or fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping JS-off reveal repair")
        return html, []

    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                context = browser.new_context(viewport=CRAFT_VIEWPORT, java_script_enabled=False)
                guard_context(context, Path(asset_root).resolve() if asset_root is not None else None)
                page = context.new_page()
                load(page, html)
                before = _measure(page)
                if not _tripped(before):
                    return html, []
                selectors: list[str] = []
                after = before
                for _ in range(_MAX_ROUNDS):
                    added = [selector for selector in _selectors(after) if selector not in selectors]
                    if not added:
                        break
                    selectors += added
                    repaired = _inject_noscript(html, selectors)
                    if repaired is None:
                        return html, []
                    load(page, repaired)
                    after = _measure(page)
                    if not _tripped(after):
                        break
                if _tripped(after):
                    logger.warning("JS-off reveal repair did not clear the hidden text, leaving HTML as-is")
                    return html, []
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping JS-off reveal repair", exc_info=True)
        return html, []

    return repaired, [f"{', '.join(selectors)} shown when JavaScript is off: hidden text {before['hidden']} -> {after['hidden']} of {before['total']} chars"]
