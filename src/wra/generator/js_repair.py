import logging
import re
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path

from wra.llm.client import LLMClient

from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page

logger = logging.getLogger(__name__)

_JS_TYPES = frozenset({"", "text/javascript", "application/javascript"})
_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
_MAX_LENGTH_DELTA_RATIO = 0.10

_SYNTAX_CHECK_JS = r'''(src) => {
  const strict = /^(?:\s|\/\/[^\n]*\n|\/\*[\s\S]*?\*\/)*(['"])use strict\1/.test(src);
  try {
    (0, eval)((strict ? '"use strict";' : '') + 'throw 0;\n' + src);
    return null;
  } catch (e) {
    return e === 0 ? null : String(e);
  }
}'''

Checker = Callable[[str], str | None]


class _InlineScriptCollector(HTMLParser):
    """Record the span and source of each inline classic (non-module, JS) script."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._in_target_script = False
        self.scripts: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attr_map = dict(attrs)
        if attr_map.get("src") is not None:
            return
        script_type = (attr_map.get("type") or "").strip().lower()
        if script_type not in _JS_TYPES:
            return
        self._in_target_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_target_script:
            self._in_target_script = False

    def handle_data(self, data: str) -> None:
        if self._in_target_script:
            self.scripts.append((*self.getpos(), data))


def _offset(line_starts: list[int], pos: tuple[int, int]) -> int:
    lineno, col = pos
    return line_starts[lineno - 1] + col


def _line_starts(text: str) -> list[int]:
    starts = [0]
    index = text.find("\n")
    while index != -1:
        starts.append(index + 1)
        index = text.find("\n", index + 1)
    return starts


def find_inline_scripts(html: str) -> list[tuple[int, int, str]]:
    """Return `(start_offset, end_offset, source)` for each inline classic JS script."""
    collector = _InlineScriptCollector(html)
    collector.feed(html)
    starts = _line_starts(html)
    spans: list[tuple[int, int, str]] = []
    for lineno, col, source in collector.scripts:
        start = _offset(starts, (lineno, col))
        spans.append((start, start + len(source), source))
    return spans


def default_syntax_checker(html: str) -> dict[str, str | None]:
    """Check every inline classic JS script in `html`, returning `{source: error_or_None}`.

    Launches one headless Chromium browser for all scripts in `html`. Returns an
    empty dict (nothing to repair) if Playwright is unavailable or fails to launch.
    """
    scripts = find_inline_scripts(html)
    if not scripts:
        return {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping JS syntax repair")
        return {}

    results: dict[str, str | None] = {}
    try:
        with sync_playwright() as playwright:
            browser = launch_guarded(playwright)
            try:
                context = new_guarded_page(browser, None).context
                for _, _, source in scripts:
                    if source in results:
                        continue
                    page = context.new_page()
                    try:
                        results[source] = page.evaluate(_SYNTAX_CHECK_JS, source)
                    finally:
                        page.close()
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping JS syntax repair", exc_info=True)
        return {}
    return results


def _extract_fenced_code(response: str) -> str | None:
    match = _FENCE_RE.search(response)
    if match:
        return match.group(1).strip()
    stripped = response.strip()
    return stripped or None


def repair_js_syntax(
    html: str,
    llm_client: LLMClient,
    model: str,
    prompt_path: str | Path = "prompts/generator/js_repair.md",
    *,
    checker: Checker | None = None,
    think: bool | None = None,
    timeout: float | None = None,
) -> tuple[str, list[str]]:
    """Repair inline scripts with a JS syntax error via one LLM call per broken script.

    Deterministic parsing cannot distinguish a corrupted token from an intentional
    one; the syntax checker only locates broken scripts. A replacement is applied
    only when it passes the same checker and stays within `_MAX_LENGTH_DELTA_RATIO`
    of the original length; any LLM failure or rejected replacement leaves that
    script unchanged for detection and scoring to handle.
    """
    check = checker or default_syntax_checker
    try:
        errors = check(html)
    except Exception:
        logger.warning("JS syntax check failed, leaving scripts as-is", exc_info=True)
        return html, []

    broken = {source: error for source, error in errors.items() if error}
    if not broken:
        return html, []

    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    spans = find_inline_scripts(html)
    repairs: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, source in spans:
        error = broken.get(source)
        if error is None:
            continue
        pieces.append(html[cursor:start])
        prompt = f"{prompt_template}\n\nScript:\n```javascript\n{source}\n```\n\nError:\n{error}"
        try:
            response = llm_client.generate(prompt=prompt, model=model, think=think, timeout=timeout)
            replacement = _extract_fenced_code(response)
        except Exception:
            logger.warning("JS syntax repair call failed, leaving script as-is: %s", error)
            pieces.append(source)
            cursor = end
            continue
        if not replacement:
            logger.warning("JS syntax repair returned no usable replacement for error: %s", error)
            pieces.append(source)
            cursor = end
            continue
        if len(source) and abs(len(replacement) - len(source)) / len(source) > _MAX_LENGTH_DELTA_RATIO:
            logger.warning("JS syntax repair rejected an oversized rewrite for error: %s", error)
            pieces.append(source)
            cursor = end
            continue
        try:
            replacement_error = check(f"<script>{replacement}</script>").get(replacement)
        except Exception:
            replacement_error = "checker failed"
        if replacement_error:
            logger.warning("JS syntax repair replacement still fails to parse: %s", replacement_error)
            pieces.append(source)
            cursor = end
            continue
        pieces.append(replacement)
        cursor = end
        repairs.append(f"fixed: {error}")

    pieces.append(html[cursor:])
    if not repairs:
        return html, []
    return "".join(pieces), repairs
