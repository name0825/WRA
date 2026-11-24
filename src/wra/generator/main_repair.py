import logging
import re

from wra.validator.structure import validate_structure

logger = logging.getLogger(__name__)

_MASKED_RE = re.compile(r"<!--.*?-->|<(script|style)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*?(/?)>")
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_IMPLIED_END = frozenset({"p", "li", "dt", "dd", "option", "optgroup", "tr", "td", "th", "thead", "tbody", "tfoot", "colgroup", "caption", "rt", "rp"})
_CHROME =frozenset({"header", "nav", "footer"})
_CONTENT = frozenset({"section", "article"})


def _mask(html: str) -> str:
    return _MASKED_RE.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), html)


def repair_missing_main(html: str) -> tuple[str, list[str]]:
    masked = _mask(html)
    if re.search(r"<main\b", masked, re.IGNORECASE):
        return html, []
    body = re.search(r"<body\b[^>]*>", masked, re.IGNORECASE)
    body_close = masked.lower().rfind("</body")
    if body is None or body_close < body.end():
        return html, []

    depth = 0
    start = None
    end = body_close
    for match in _TAG_RE.finditer(masked, body.end(), body_close):
        closing, name, self_closing = match.group(1), match.group(2).lower(), match.group(3)
        if name in _VOID or name in _IMPLIED_END or self_closing:
            continue
        if closing:
            depth = max(depth - 1, 0)
            continue
        if depth == 0:
            if start is None and name in _CONTENT:
                start = match.start()
            elif start is not None and name in _CHROME:
                if name != "footer":
                    return html, []
                end = match.start()
                break
        depth += 1
    if start is None:
        return html, []
    if end == body_close and re.search(r"<footer\b", masked[start:body_close], re.IGNORECASE):
        return html, []

    result = f"{html[:start]}<main>{html[start:end]}</main>{html[end:]}"
    errors, _ = validate_structure(result)
    errors_before, _ = validate_structure(html)
    if len(errors) > len(errors_before) or re.search(r"<main\b", _mask(result), re.IGNORECASE) is None:
        return html, []
    return result, ["wrapped content sections in <main>"]
