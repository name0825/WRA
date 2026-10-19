import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
# CSS comments and string literals may legitimately contain `: #...` text
# (a commented-out rule, `content: "#new"`) that is not a live value.
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)
# Only a value position (right after `:`) is checked, never a selector or a
# `url(#fragment)` reference, since both of those legitimately start with `#`.
_HEX_VALUE_RE = re.compile(r":\s*(#[0-9A-Za-z]{2,10})\b")
_VALID_HEX_LENGTHS = {3, 4, 6, 8}
_HEX_DIGITS = set("0123456789abcdefABCDEF")
# A digit run, a hyphen, another digit run, then a unit: the exact shape a
# decimal point turns into when a digit gets retyped as a hyphen.
_HYPHEN_NUMERIC_RE = re.compile(r"(?<![\w.-])[0-9]+-[0-9]+(?:rem|px|em|%)\b")

_KNOWN_HREF_SCHEMES = {"http", "https", "mailto", "tel", "sms", "geo", "fax"}
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")

_CUSTOM_PROP_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
_CUSTOM_PROP_TOKEN_RE = re.compile(r"--[A-Za-z0-9_-]+")
_VAR_REF_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,?)")
_AT_PROPERTY_RE = re.compile(r"@property\s+(--[A-Za-z0-9_-]+)")
_SCRIPT_BLOCK_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _extract_css_text(html: str) -> str:
    raw = "\n".join(_STYLE_BLOCK_RE.findall(html)) + "\n" + "\n".join(_STYLE_ATTR_RE.findall(html))
    return _STRING_RE.sub(" ", _COMMENT_RE.sub(" ", raw))


def _is_valid_hex_color(hex_part: str) -> bool:
    return len(hex_part) in _VALID_HEX_LENGTHS and all(character in _HEX_DIGITS for character in hex_part)


def find_malformed_css_values(html: str) -> list[str]:
    """Report CSS values an LLM retyped into syntax the browser silently drops.

    A hex colour with a letter outside a-f, or a decimal point retyped as a
    hyphen, both parse as invalid and void the declaration -- damage a plain
    HTML read misses because the markup itself is well-formed.
    """
    css_text = _extract_css_text(html)
    findings: list[str] = []
    seen: set[str] = set()

    for match in _HEX_VALUE_RE.finditer(css_text):
        token = match.group(1)
        if token in seen or _is_valid_hex_color(token[1:]):
            continue
        seen.add(token)
        findings.append(f'malformed CSS colour value "{token}" is not a valid hex colour')

    for match in _HYPHEN_NUMERIC_RE.finditer(css_text):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        findings.append(f'malformed CSS numeric value "{token}" uses a hyphen where a decimal point belongs')

    if findings:
        logger.warning("Malformed CSS values detected: %s", findings)
    return findings


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def find_invalid_href_schemes(html: str) -> list[str]:
    """Report `href` values whose scheme is not one the page is allowed to use.

    A scheme garbled by retyping (`html:someone@example.com` instead of
    `mailto:someone@example.com`) still parses as a URL, so nothing else
    catches it; the link simply does nothing when a user activates it.
    """
    parser = _HrefParser()
    parser.feed(html)

    findings: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        if not href or href.startswith("#") or href.startswith("//"):
            continue
        match = _SCHEME_RE.match(href)
        if not match:
            continue
        scheme = match.group(1).lower()
        if scheme in _KNOWN_HREF_SCHEMES or href in seen:
            continue
        seen.add(href)
        if scheme == "javascript":
            findings.append(f'link href "{href}" uses a "javascript:" scheme, reported as a defect on purpose')
        else:
            findings.append(f'link href "{href}" uses unknown scheme "{scheme}:" (expected http, https, mailto, tel, sms, geo, fax, #fragment, or a relative path)')

    if findings:
        logger.warning("Invalid href schemes detected: %s", findings)
    return findings


class _StylesheetLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_non_font_stylesheet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attr_dict = dict(attrs)
        rel = (attr_dict.get("rel") or "").lower().split()
        if "stylesheet" not in rel:
            return
        href = attr_dict.get("href") or ""
        if "fonts.googleapis.com" not in href:
            self.has_non_font_stylesheet = True


def find_undefined_custom_properties(html: str) -> list[str]:
    """Report `var(--name)` references (with no fallback) whose `--name` is never declared.

    A retyped custom property (`var(---border)` instead of `var(--border)`)
    still parses, so the declaration it should resolve to silently never
    applies. Skipped entirely when a non-font stylesheet is linked, since a
    framework CSS file declares its own custom properties this page never
    sees.
    """
    link_parser = _StylesheetLinkParser()
    link_parser.feed(html)
    if link_parser.has_non_font_stylesheet:
        return []

    css_text = _extract_css_text(html)
    declared = set(_CUSTOM_PROP_DECL_RE.findall(css_text))
    declared.update(_AT_PROPERTY_RE.findall(css_text))
    for script_text in _SCRIPT_BLOCK_RE.findall(html):
        declared.update(_CUSTOM_PROP_TOKEN_RE.findall(script_text))

    undefined: list[str] = []
    seen: set[str] = set()
    for match in _VAR_REF_RE.finditer(css_text):
        name, has_fallback = match.group(1), match.group(2)
        if has_fallback or name in declared or name in seen:
            continue
        seen.add(name)
        undefined.append(name)

    if not undefined:
        return []
    examples = ", ".join(f'"{name}"' for name in undefined[:3])
    label = "custom property" if len(undefined) == 1 else "custom properties"
    findings = [f"undefined CSS {label} referenced with no fallback: {len(undefined)} found, e.g. {examples}"]
    logger.warning("Undefined CSS custom properties detected: %s", findings)
    return findings


def find_css_truncation(html: str) -> list[str]:
    """Report a `<style>`/`style=` block whose braces do not balance, a sign generation cut off mid-declaration.

    An unclosed rule (`h2 { font-size: clamp(1.8rem, 5vw, 2.2`) leaves every
    later selector unparsed, so the page renders effectively unstyled from
    that point on -- damage a plain HTML read misses because the markup
    itself is well-formed.
    """
    css_text = _extract_css_text(html)
    if not css_text.strip():
        return []
    open_count = css_text.count("{")
    close_count = css_text.count("}")
    if open_count == close_count:
        return []
    findings = [f"CSS braces are unbalanced ({open_count} open, {close_count} close), the stylesheet looks truncated mid-declaration"]
    logger.warning("CSS truncation detected: %s", findings)
    return findings


def find_syntax_defects(html: str) -> list[str]:
    """Combine the malformed-CSS, bad-href-scheme, and undefined-custom-property checks into one findings list."""
    return find_malformed_css_values(html) + find_invalid_href_schemes(html) + find_undefined_custom_properties(html)
