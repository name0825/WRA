import colorsys
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

from wra.browser.contrast import (
    CONTRAST_CANDIDATES_SCRIPT,
    LARGE_TEXT_MIN_RATIO,
    NORMAL_TEXT_MIN_RATIO,
    RGB_HELPER_JS,
    contrast_failures,
    contrast_ratio,
    is_large_text,
    parse_css_color,
)
from wra.browser.reveal import reveal_animated_content

from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_WIDTHS = (1280, 390)
_REVEAL_TIMEOUT_MS = 10000
_LOAD_TIMEOUT_MS = 10000
_MARGIN = 0.1
_STEP = 0.01
_SKIP_TAGS = {"html", "head", "meta", "link", "title", "style", "script", "base"}
_BLOCK_RE = re.compile(r'(<style data-wra-repair="contrast">\n)(.*?)(\n</style>)', re.DOTALL)
_MARKER_RE = re.compile(r'data-wra-contrast="(\d+)"')

_CONTAINING_SCRIPT = """([newly, candidates]) => {
  const find = (i) => document.querySelector(`[data-wra-i="${i}"]`);
  const failing = newly.map(find).filter(Boolean);
  return candidates.filter(i => {
    const el = find(i);
    return el && failing.some(other => el.contains(other));
  });
}"""

_OWN_STYLE_SCRIPT = """(indexes) => {
""" + RGB_HELPER_JS + """
  return indexes.map(i => {
    const el = document.querySelector(`[data-wra-i="${i}"]`);
    if (!el) return null;
    const inline = el.getAttribute('style') || '';
    return [rgb(getComputedStyle(el).backgroundColor), /(^|;)\\s*color\\s*:/i.test(inline), /(^|;)\\s*background(-color)?\\s*:/i.test(inline)];
  });
}"""


class _StartTagOffsets(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_starts = [0] + [match.end() for match in re.finditer("\n", html)]
        self.offsets: list[int] = []

    def _record(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            return
        line, column = self.getpos()
        self.offsets.append(self._line_starts[line - 1] + column + 1 + len(tag))

    def handle_starttag(self, tag, attrs):
        self._record(tag)

    def handle_startendtag(self, tag, attrs):
        self._record(tag)


def _offsets(html: str) -> list[int]:
    parser = _StartTagOffsets(html)
    parser.feed(html)
    parser.close()
    return parser.offsets


def _annotate(html: str, offsets: list[int], markers: dict[int, int], tag_all: bool) -> str:
    for index in range(len(offsets) - 1, -1, -1):
        attrs = f' data-wra-i="{index}"' if tag_all else ""
        if index in markers:
            attrs += f' data-wra-contrast="{markers[index]}"'
        if attrs:
            html = html[: offsets[index]] + attrs + html[offsets[index]:]
    return html


def _inject_rules(html: str, rules: list[str]) -> str:
    content = "\n".join(rules)
    match = _BLOCK_RE.search(html)
    if match:
        return f"{html[:match.end(2)]}\n{content}{html[match.end(2):]}"
    block = f'<style data-wra-repair="contrast">\n{content}\n</style>'
    closing = list(re.finditer(r"</body>", html, flags=re.IGNORECASE))
    if closing:
        at = closing[-1].start()
        return f"{html[:at]}{block}\n{html[at:]}"
    return f"{html}\n{block}"


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(value):02x}" for value in rgb)


def _rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(float(int(hex_color[index:index + 2], 16)) for index in (1, 3, 5))


def _luminance(rgb: tuple[float, float, float]) -> float:
    return contrast_ratio(rgb, (0.0, 0.0, 0.0)) * 0.05 - 0.05


def _lighten_away(foreground: tuple[float, float, float], background: tuple[float, float, float]) -> bool:
    fg, bg = _luminance(foreground), _luminance(background)
    if abs(fg - bg) < 1e-6:
        return bg <= 0.179
    return fg > bg


def _move(color: tuple[float, float, float], other: tuple[float, float, float], lighten: bool, target: float):
    hue, light, saturation = colorsys.rgb_to_hls(*(value / 255 for value in color))
    step = _STEP if lighten else -_STEP
    while True:
        light = min(max(light + step, 0.0), 1.0)
        moved = tuple(round(value * 255) for value in colorsys.hls_to_rgb(hue, light, saturation))
        if contrast_ratio(moved, other) >= target:
            return moved
        if light in (0.0, 1.0):
            return None


def _failing(candidate: dict) -> dict | None:
    failures = contrast_failures([candidate])
    if not failures or candidate.get("idx") is None:
        return None
    failure = failures[0]
    large = is_large_text(float(candidate.get("size") or 0), float(candidate.get("weight") or 400))
    return {
        "text": failure.text,
        "ratio": failure.ratio,
        "fg": _rgb(failure.foreground),
        "bg": _rgb(failure.background),
        "minimum": LARGE_TEXT_MIN_RATIO if large else NORMAL_TEXT_MIN_RATIO,
        "top_layer": str(candidate["layers"][0]) if candidate["layers"] else None,
    }


def _find_failures(page, html: str, load) -> dict[int, dict]:
    found: dict[int, dict] = {}
    page.set_viewport_size({"width": _WIDTHS[0], "height": 800})
    load(page, html, wait_until="domcontentloaded", timeout=_LOAD_TIMEOUT_MS)
    reveal_animated_content(page, _REVEAL_TIMEOUT_MS)
    for width in _WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        current = {}
        for candidate in page.evaluate(CONTRAST_CANDIDATES_SCRIPT):
            failure = _failing(candidate)
            if failure is not None:
                current.setdefault(int(candidate["idx"]), failure)
        if not current:
            continue
        indexes = list(current)
        for index, own in zip(indexes, page.evaluate(_OWN_STYLE_SCRIPT, indexes)):
            background, inline_color, inline_background = own or ("", False, False)
            parsed = parse_css_color(background) if background else None
            current[index]["owned"] = background == current[index]["top_layer"] and parsed is not None and parsed[3] == 1.0
            current[index]["inline_color"], current[index]["inline_background"] = inline_color, inline_background
        for index, failure in current.items():
            if index not in found or failure["minimum"] > found[index]["minimum"]:
                found[index] = failure
    return found


def _plan_groups(failures: dict[int, dict], first_number: int) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for index, failure in failures.items():
        key = (failure["fg"], failure["bg"], failure["minimum"], failure["owned"], failure["inline_color"], failure["inline_background"])
        group = groups.setdefault(key, {**failure, "indexes": []})
        group["indexes"].append(index)
    planned = []
    for group in groups.values():
        fg, bg, target = group["fg"], group["bg"], group["minimum"] + _MARGIN
        lighten = _lighten_away(fg, bg)
        new_fg = _move(fg, bg, lighten, target)
        if new_fg is None and not lighten:
            new_fg = _move(fg, bg, True, target)
        new_bg = None if new_fg else (_move(bg, fg, not lighten, target) if group["owned"] else None)
        if new_fg is None and new_bg is None:
            logger.warning("Contrast repair skipped '%s' %s/%s %.2f: no verifiable colour change", group["text"], _hex(fg), _hex(bg), group["ratio"])
            continue
        group["new_fg"], group["new_bg"] = new_fg, new_bg
        group["number"] = first_number + len(planned)
        planned.append(group)
    return planned


def _rule(group: dict) -> str:
    if group["new_fg"]:
        declaration = f"color:{_hex(group['new_fg'])} !important"
    else:
        declaration = f"background-color:{_hex(group['new_bg'])} !important"
    return f'[data-wra-contrast="{group["number"]}"][data-wra-contrast]:not(:hover){{{declaration}}}'


def _entry(group: dict) -> str:
    fg, bg = group["fg"], group["bg"]
    if group["new_fg"]:
        after, change = contrast_ratio(group["new_fg"], bg), f"fg {_hex(group['new_fg'])}"
    else:
        after, change = contrast_ratio(fg, group["new_bg"]), f"bg {_hex(group['new_bg'])}"
    count = f" x{len(group['indexes'])}" if len(group["indexes"]) > 1 else ""
    return f"'{group['text']}' {_hex(fg)}/{_hex(bg)} {group['ratio']:.2f}->{after:.2f} ({change}){count}"


def _build(html: str, offsets: list[int], groups: list[dict], tag_all: bool) -> str:
    markers = {index: group["number"] for group in groups for index in group["indexes"]}
    return _inject_rules(_annotate(html, offsets, markers, tag_all), [_rule(group) for group in groups])


def repair_low_contrast(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Recolour text that fails WCAG AA contrast, keeping the hue.

    Source start tags are tagged with a temporary index so the failing text
    elements found by the browser contrast detector (desktop and mobile
    widths) map back to exact source elements. Each distinct failing
    foreground/background pair gets one new colour: the foreground moves away
    from the background in small HSL-lightness steps until the required ratio
    plus a margin is met, or, when the foreground runs out of range, the
    element's own opaque background moves instead. Elements are marked in the
    original source and one `<style data-wra-repair="contrast">` block at the
    end of the body carries the rules (skipped while hovered, so hover styles
    still apply). A re-render verifies the result; groups whose elements still
    fail are dropped, and so are groups whose element is, or contains, an
    element that started failing (colour inherits into descendants). Elements that exist only after scripts run
    cannot be mapped and are left alone. Skips gracefully (returns the input
    unchanged) if Playwright is unavailable or fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping contrast repair")
        return html, []

    try:
        offsets = _offsets(html)
        if not offsets:
            return html, []
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                before = _find_failures(page, _annotate(html, offsets, {}, True), load)
                if not before:
                    return html, []
                first_number = max((int(number) for number in _MARKER_RE.findall(html)), default=0) + 1
                groups = _plan_groups(before, first_number)
                if not groups:
                    return html, []
                after = _find_failures(page, _build(html, offsets, groups, True), load)
                newly_failing = set(after) - set(before)
                containing = set()
                if newly_failing:
                    every_index = [index for group in groups for index in group["indexes"]]
                    containing = set(page.evaluate(_CONTAINING_SCRIPT, [sorted(newly_failing), every_index]))
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping contrast repair", exc_info=True)
        return html, []

    kept = []
    for group in groups:
        if any(index in after for index in group["indexes"]):
            logger.warning("Contrast repair did not verify for '%s', dropping it", group["text"])
        elif containing.intersection(group["indexes"]):
            logger.warning("Contrast repair for '%s' broke other text, dropping it", group["text"])
        else:
            kept.append(group)
    if not kept:
        return html, []
    return _build(html, offsets, kept, False), [_entry(group) for group in kept]
