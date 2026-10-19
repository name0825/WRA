import logging
import re
from pathlib import Path

from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_V4_BROWSER_RE = re.compile(r"@tailwindcss/browser@4", re.IGNORECASE)
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>(?:(?!<script\b).)*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RE = re.compile(r'\bsrc\s*=\s*(["\'])(?P<url>.*?)\1', re.IGNORECASE | re.DOTALL)
_TYPE_ATTR_RE = re.compile(r'\btype\s*=\s*(["\'])(?P<type>.*?)\1', re.IGNORECASE | re.DOTALL)
_CDN_HOST_RE = re.compile(r"^https?://cdn\.tailwindcss\.com", re.IGNORECASE)
_CONFIG_ASSIGN_RE = re.compile(r"tailwind\s*\.\s*config\s*=")
_CONFIG_GUARD = "window.tailwind = window.tailwind || {};"
_THEME_BLOCK_RE = re.compile(r"@theme\s*\{")
_STYLE_TAILWINDCSS_RE = re.compile(r'<style\s+type\s*=\s*(["\'])text/tailwindcss\1[^>]*>.*?</style\s*>', re.IGNORECASE | re.DOTALL)
_APPLY_LINE_RE = re.compile(r"(?P<indent>[ \t]*)@apply\s+(?P<tokens>[^;]+);(?P<trail>[ \t]*\r?\n?)")
_UNKNOWN_UTILITY_RE = re.compile(r"Cannot apply unknown utility class `(?P<cls>[^`]+)`")
_V4_UTILITY_RENAMES = {
    "overflow-wrap-anywhere": "wrap-anywhere",
    "flex-grow": "grow",
    "flex-shrink": "shrink",
    "overflow-ellipsis": "text-ellipsis",
    "decoration-slice": "box-decoration-slice",
    "decoration-clone": "box-decoration-clone",
}
_MAX_APPLY_REPAIR_ROUNDS = 3
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_FORBIDDEN_VALUE_RE = re.compile(r"[;{}<]")
_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-/]+$")
_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<linecomment>//[^\n]*)
    | (?P<blockcomment>/\*.*?\*/)
    | (?P<dstring>"(?:\\.|[^"\\])*")
    | (?P<sstring>'(?:\\.|[^'\\])*')
    | (?P<tstring>`(?:\\.|[^`\\])*`)
    | (?P<punct>[{}\[\]():,])
    | (?P<word>[^\s{}\[\]():,'"`]+)
    """,
    re.VERBOSE | re.DOTALL,
)
_SIMPLE_THEME_CATEGORIES = {
    "colors": "color",
    "spacing": "spacing",
    "maxWidth": "container",
    "borderRadius": "radius",
    "boxShadow": "shadow",
}

_NON_LITERAL = object()


class _Unparseable(Exception):
    pass


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return n


def _find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in ("'", '"', "`"):
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else n
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    n = len(text)
    while pos < n:
        match = _TOKEN_RE.match(text, pos)
        if not match:
            pos += 1
            continue
        pos = match.end()
        kind = match.lastgroup
        if kind in ("ws", "linecomment", "blockcomment"):
            continue
        tokens.append((kind, match.group()))
    return tokens


def _unquote(text: str) -> str:
    return text[1:-1]


class _TokenParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> tuple[str, str]:
        if self.pos >= len(self.tokens):
            raise _Unparseable("unexpected end of input")
        return self.tokens[self.pos]

    def _advance(self) -> tuple[str, str]:
        token = self._peek()
        self.pos += 1
        return token

    def _peek_is_punct(self, ch: str) -> bool:
        return self.pos < len(self.tokens) and self.tokens[self.pos] == ("punct", ch)

    def _expect_punct(self, ch: str) -> None:
        kind, text = self._advance()
        if kind != "punct" or text != ch:
            raise _Unparseable(f"expected {ch!r}")

    def parse_object(self) -> dict:
        self._expect_punct("{")
        obj: dict = {}
        if self._peek_is_punct("}"):
            self._advance()
            return obj
        while True:
            kind, text = self._advance()
            if kind in ("dstring", "sstring"):
                key = _unquote(text)
            elif kind == "word":
                key = text
            else:
                raise _Unparseable("bad object key")
            self._expect_punct(":")
            obj[key] = self.parse_value()
            kind, text = self._advance()
            if kind == "punct" and text == "}":
                return obj
            if kind == "punct" and text == ",":
                if self._peek_is_punct("}"):
                    self._advance()
                    return obj
                continue
            raise _Unparseable("bad object separator")

    def parse_array(self) -> list:
        self._expect_punct("[")
        items: list = []
        if self._peek_is_punct("]"):
            self._advance()
            return items
        while True:
            items.append(self.parse_value())
            kind, text = self._advance()
            if kind == "punct" and text == "]":
                return items
            if kind == "punct" and text == ",":
                if self._peek_is_punct("]"):
                    self._advance()
                    return items
                continue
            raise _Unparseable("bad array separator")

    def parse_value(self):
        kind, text = self._peek()
        if kind == "punct" and text == "{":
            return self.parse_object()
        if kind == "punct" and text == "[":
            return self.parse_array()
        if kind in ("dstring", "sstring"):
            self._advance()
            return _unquote(text)
        if kind == "word" and _NUMBER_RE.match(text):
            self._advance()
            return text
        return self._skip_non_literal()

    def _skip_non_literal(self):
        depth = 0
        while True:
            kind, text = self._peek()
            if kind == "punct" and text in "([{":
                depth += 1
                self._advance()
                continue
            if kind == "punct" and text in ")]}":
                if depth == 0:
                    return _NON_LITERAL
                depth -= 1
                self._advance()
                continue
            if kind == "punct" and text == "," and depth == 0:
                return _NON_LITERAL
            self._advance()


def _is_plain_value(value: object) -> bool:
    return isinstance(value, str) and not _FORBIDDEN_VALUE_RE.search(value)


def _escape_key_segment(key: str) -> str | None:
    if not _KEY_SEGMENT_RE.match(key):
        return None
    return key.replace(".", r"\.").replace("/", r"\/")


def _flatten_theme_dict(prefix: str, value: object, out: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            if key == "DEFAULT":
                _flatten_theme_dict(prefix, sub, out)
                continue
            escaped = _escape_key_segment(key)
            if escaped is None:
                continue
            _flatten_theme_dict(f"{prefix}-{escaped}", sub, out)
    elif _is_plain_value(value):
        out[f"--{prefix}"] = value


def _maybe_quote_font_name(name: str) -> str:
    if " " not in name:
        return name
    if name[:1] in ("'", '"') and name[-1:] == name[:1]:
        return name
    return f'"{name}"'


def _font_family_tokens(value: object, out: dict[str, str]) -> None:
    if not isinstance(value, dict):
        return
    for key, sub in value.items():
        if not isinstance(sub, list) or not sub:
            continue
        escaped = _escape_key_segment(key)
        if escaped is None:
            continue
        parts = []
        for item in sub:
            if not _is_plain_value(item):
                parts = None
                break
            parts.append(_maybe_quote_font_name(item))
        if parts:
            out[f"--font-{escaped}"] = ", ".join(parts)


def _font_size_tokens(value: object, out: dict[str, str]) -> None:
    if not isinstance(value, dict):
        return
    for key, sub in value.items():
        first = sub[0] if isinstance(sub, list) and sub else sub
        escaped = _escape_key_segment(key)
        if escaped is not None and _is_plain_value(first):
            out[f"--text-{escaped}"] = first


def _merge_theme_category(theme: dict, extend: dict, name: str):
    base = theme.get(name)
    ext = extend.get(name)
    base = base if isinstance(base, dict) else None
    ext = ext if isinstance(ext, dict) else None
    if base is None:
        return ext
    if ext is None:
        return base
    merged = dict(base)
    merged.update(ext)
    return merged


def _theme_tokens_from_config(obj_text: str) -> dict[str, str]:
    tokens = _tokenize(obj_text)
    root = _TokenParser(tokens).parse_object()
    theme = root.get("theme")
    theme = theme if isinstance(theme, dict) else {}
    extend = theme.get("extend")
    extend = extend if isinstance(extend, dict) else {}

    out: dict[str, str] = {}
    for config_key, css_prefix in _SIMPLE_THEME_CATEGORIES.items():
        merged = _merge_theme_category(theme, extend, config_key)
        if isinstance(merged, dict):
            _flatten_theme_dict(css_prefix, merged, out)

    _font_family_tokens(_merge_theme_category(theme, extend, "fontFamily"), out)
    _font_size_tokens(_merge_theme_category(theme, extend, "fontSize"), out)
    return out


def _build_theme_block(html: str) -> tuple[str | None, list[str]]:
    if _THEME_BLOCK_RE.search(html):
        return None, []
    match = _CONFIG_ASSIGN_RE.search(html)
    if not match:
        return None, []
    try:
        i = match.end()
        n = len(html)
        while i < n and html[i].isspace():
            i += 1
        if i >= n or html[i] != "{":
            return None, []
        close = _find_matching_brace(html, i)
        if close == -1:
            return None, []
        tokens_map = _theme_tokens_from_config(html[i : close + 1])
    except Exception:
        return None, []
    if not tokens_map:
        return None, []
    lines = "\n".join(f"  {name}: {value};" for name, value in tokens_map.items())
    style = f'<style type="text/tailwindcss">\n@theme {{\n{lines}\n}}\n</style>'
    message = f"translated tailwind.config theme into a v4 @theme block ({len(tokens_map)} tokens)"
    return style, [message]


def _insert_before_v4_script(html: str, style_block: str) -> str:
    for tag_match in re.finditer(r"<script\b[^>]*>", html, re.IGNORECASE):
        if _V4_BROWSER_RE.search(tag_match.group(0)):
            idx = tag_match.start()
            return html[:idx] + style_block + "\n" + html[idx:]
    return html


def repair_tailwind_v3_remnants(html: str) -> tuple[str, list[str]]:
    """Strip Tailwind v3 Play CDN leftovers from a page that loads the v4 browser build.

    A generated page occasionally mixes in Tailwind v3 artifacts -- the Play
    CDN `<script src="https://cdn.tailwindcss.com">` (blocked by the sandboxed
    browser's request filtering) and an inline `tailwind.config = {...}`
    assignment (throws `tailwind is not defined` once the v3 script is gone).
    Both are only a problem alongside the v4 browser build, so pages that
    don't load `@tailwindcss/browser@4` are returned unchanged.
    """
    if not _V4_BROWSER_RE.search(html):
        return html, []

    applied: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        open_end = tag.find(">") + 1
        opening_tag = tag[:open_end]
        type_match = _TYPE_ATTR_RE.search(opening_tag)
        if type_match and type_match.group("type").strip().lower() == "text/tailwindcss":
            return tag
        src_match = _SRC_ATTR_RE.search(opening_tag)
        if src_match:
            if _CDN_HOST_RE.match(src_match.group("url").strip()):
                applied.append(f"removed Tailwind v3 CDN script: {src_match.group('url')}")
                return ""
            return tag
        if _CONFIG_ASSIGN_RE.search(tag) and _CONFIG_GUARD not in tag:
            close_start = tag.rfind("</script")
            body = tag[open_end:close_start]
            applied.append("guarded inline tailwind.config assignment against the v4 browser build")
            return f"{opening_tag}\n{_CONFIG_GUARD}{body}{tag[close_start:]}"
        return tag

    repaired = _SCRIPT_TAG_RE.sub(_replace, html)

    style_block, theme_applied = _build_theme_block(repaired)
    if style_block:
        repaired = _insert_before_v4_script(repaired, style_block)
        applied.extend(theme_applied)

    return repaired, applied


def _remove_apply_token(html: str, token: str) -> str:
    """Remove or rename `token` in every `@apply` line inside `text/tailwindcss` style blocks."""
    replacement = _V4_UTILITY_RENAMES.get(token)

    def _replace_apply(match: re.Match[str]) -> str:
        tokens = match.group("tokens").split()
        if token not in tokens:
            return match.group(0)
        if replacement:
            new_tokens = [replacement if t == token else t for t in tokens]
        else:
            new_tokens = [t for t in tokens if t != token]
        if not new_tokens:
            return ""
        return f"{match.group('indent')}@apply {' '.join(new_tokens)};{match.group('trail')}"

    def _replace_style_block(style_match: re.Match[str]) -> str:
        return _APPLY_LINE_RE.sub(_replace_apply, style_match.group(0))

    return _STYLE_TAILWINDCSS_RE.sub(_replace_style_block, html)


def repair_unknown_apply_utilities(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Fix `@apply` utilities the Tailwind v4 browser build doesn't recognize.

    A single unknown utility in an `@apply` line makes the v4 browser build drop
    the whole `text/tailwindcss` style block it appears in, leaving every other
    rule in that block unstyled. Renders the page, finds the unknown-utility
    page error, and either renames the token to its v4 equivalent (for known v3
    renames) or drops it from the `@apply` line (dropping the line entirely if
    it ends up empty). Repeats for up to `_MAX_APPLY_REPAIR_ROUNDS` rounds since
    Tailwind reports one such error per compile. Returns the input unchanged if
    the page doesn't use the v4 browser build with `@apply`, or if Playwright is
    unavailable or a render fails.
    """
    if not _V4_BROWSER_RE.search(html):
        return html, []
    if not any("@apply" in match.group(0) for match in _STYLE_TAILWINDCSS_RE.finditer(html)):
        return html, []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping unknown @apply utility repair")
        return html, []

    applied: list[str] = []
    current = html
    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                for _ in range(_MAX_APPLY_REPAIR_ROUNDS):
                    page = new_guarded_page(browser, asset_root)
                    errors: list[str] = []
                    page.on("pageerror", lambda exc: errors.append(str(exc)))
                    try:
                        load(page, current)
                        page.wait_for_timeout(300)
                    finally:
                        page.close()
                    match = next((_UNKNOWN_UTILITY_RE.search(error) for error in errors if _UNKNOWN_UTILITY_RE.search(error)), None)
                    if not match:
                        break
                    token = match.group("cls")
                    fixed = _remove_apply_token(current, token)
                    if fixed == current:
                        break
                    current = fixed
                    if token in _V4_UTILITY_RENAMES:
                        applied.append(f"renamed unknown @apply utility `{token}` to `{_V4_UTILITY_RENAMES[token]}`")
                    else:
                        applied.append(f"removed unknown @apply utility `{token}`")
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping unknown @apply utility repair", exc_info=True)
        return html, []

    return current, applied
