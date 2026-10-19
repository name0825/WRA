import logging
import re
from html.parser import HTMLParser
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_HREF_RE = re.compile(r'(?P<prefix><a\b[^>]*?\s)href=(?P<quote>["\'])#(?P<target>[^"\']*)(?P=quote)', re.IGNORECASE)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        href = attr_dict.get("href")
        if href and href.startswith("#") and len(href) > 1:
            fragment = href[1:]
            if not fragment.startswith(("!", "/")):
                self.hrefs.append(unquote(fragment))
        element_id = attr_dict.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "a":
            name = attr_dict.get("name")
            if name:
                self.ids.add(name)


def find_broken_anchors(html: str) -> list[str]:
    """Report in-page `href="#name"` links whose target does not exist.

    A broken anchor is a usability defect, not a content defect: nothing about
    the source text is wrong, a control simply does nothing when activated.
    """
    parser = _AnchorParser()
    parser.feed(html)

    findings: list[str] = []
    seen: set[str] = set()
    for target in parser.hrefs:
        if target in parser.ids or target in seen or target.lower() == "top":
            continue
        seen.add(target)
        findings.append(f'Nav/link anchor "#{target}" points to no element with id="{target}"')

    if findings:
        logger.warning("Broken in-page anchors detected: %s", findings)
    return findings


def _tokens(element_id: str) -> set[str]:
    """Split an id on `_`/`-` into lowercased tokens."""
    return {token.lower() for token in re.split(r"[_-]", element_id) if token}


def repair_broken_anchors(html: str) -> tuple[str, list[str]]:
    """Rewrite a broken `href="#x"` to the one existing id that names it unambiguously.

    A section id such as `pricing_tiers` names the anchor `#pricing` when
    `pricing` is one of its `_`/`-` separated tokens. When exactly one existing
    id matches this way, the href is repaired; when zero or several match, it
    is left broken rather than guessed at.
    """
    parser = _AnchorParser()
    parser.feed(html)

    rewrites: dict[str, str] = {}
    seen: set[str] = set()
    for target in parser.hrefs:
        if target in parser.ids or target in seen or target.lower() == "top":
            continue
        seen.add(target)
        matches = [element_id for element_id in parser.ids if target.lower() in _tokens(element_id)]
        if len(matches) == 1:
            rewrites[target] = matches[0]

    if not rewrites:
        return html, []

    applied: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        target = match.group("target")
        replacement = rewrites.get(target)
        if replacement is None:
            return match.group(0)
        rewrite = f"#{target} -> #{replacement}"
        if rewrite not in applied:
            applied.append(rewrite)
        quote = match.group("quote")
        return f"{match.group('prefix')}href={quote}#{replacement}{quote}"

    repaired = _HREF_RE.sub(_replace, html)
    return repaired, applied


_URL_ATTR_RE = re.compile(r'\b(?P<name>href|src)=(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)', re.IGNORECASE)
_TAG_OR_SKIP_RE = re.compile(
    r"<(?P<skip_tag>script|style|textarea|template)\b[^>]*>.*?(?:</(?P=skip_tag)\s*>|\Z)"
    r"|<[^>]+>",
    re.IGNORECASE | re.DOTALL,
)
_TEXT_URL_RE = re.compile(r'https?://[^\s<>"\']+')
_SCHEME_RE = re.compile(r"https?://", re.IGNORECASE)
_HOST_PREFIX_RE = re.compile(r"https?://([^/]+)/", re.IGNORECASE)
_HOST_RE = re.compile(r"https?://([^/]+)", re.IGNORECASE)


def _strip_www(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _dedupe_scheme_once(value: str) -> str | None:
    match = next((m for m in _SCHEME_RE.finditer(value) if m.start() > 0), None)
    if match is None:
        return None
    outer, inner = value[: match.start()], value[match.start() :]
    if _SCHEME_RE.fullmatch(outer):
        return inner
    host_prefix_match = _HOST_PREFIX_RE.fullmatch(outer)
    inner_host_match = _HOST_RE.match(inner)
    if host_prefix_match and inner_host_match and _strip_www(host_prefix_match.group(1)) == _strip_www(inner_host_match.group(1)):
        return inner
    return None


def repair_doubled_url_schemes(html: str) -> tuple[str, list[str]]:
    """Rewrite an `href`/`src` value or visible text with an already-full URL prefixed onto it.

    A generated link like `https://github.com/https://github.com/name` has the
    real URL doubled behind a scheme (or a `scheme://host/` prefix naming the
    same host). The doubling is stripped down to the innermost URL; values
    whose outer and inner hosts differ, such as an archive.org wrapper URL,
    are left untouched. The same repair is applied to doubled URLs appearing
    in visible text nodes, skipping `<script>`, `<style>`, `<textarea>` and
    `<template>` content.
    """
    applied: list[str] = []

    def _replace_value(value: str) -> str | None:
        new_value = value
        while True:
            deduped = _dedupe_scheme_once(new_value)
            if deduped is None:
                break
            new_value = deduped
        if new_value == value:
            return None
        rewrite = f"{value} -> {new_value}"
        if rewrite not in applied:
            applied.append(rewrite)
        return new_value

    def _replace_attr(match: re.Match[str]) -> str:
        value = match.group("value")
        new_value = _replace_value(value)
        if new_value is None:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('name')}={quote}{new_value}{quote}"

    def _replace_text_url(match: re.Match[str]) -> str:
        value = match.group(0)
        return _replace_value(value) or value

    def _repair_text_nodes(text: str) -> str:
        last = 0
        pieces: list[str] = []
        for tag_match in _TAG_OR_SKIP_RE.finditer(text):
            pieces.append(_TEXT_URL_RE.sub(_replace_text_url, text[last : tag_match.start()]))
            pieces.append(tag_match.group(0))
            last = tag_match.end()
        pieces.append(_TEXT_URL_RE.sub(_replace_text_url, text[last:]))
        return "".join(pieces)

    repaired = _URL_ATTR_RE.sub(_replace_attr, html)
    repaired = _repair_text_nodes(repaired)
    return repaired, applied
