import re
from html import escape

from wra.validator.visual import _references_image

_MASKED_RE = re.compile(r"<!--.*?-->|<(script|style)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_HEIGHT = 40


def _mask(html: str) -> str:
    return _MASKED_RE.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), html)


def insert_missing_logo(html: str, logo: str, alt: str = "") -> tuple[str, list[str]]:
    reference = logo.replace("\\", "/")
    if _references_image(html, reference) or reference in html:
        return html, []
    image = f'<img src="{escape(reference, quote=True)}" alt="{escape(alt.strip() or "logo", quote=True)}" class="site-logo" style="height:{_HEIGHT}px;width:auto;max-width:100%">'
    masked = _mask(html)
    for tag in ("header", "nav"):
        opening = re.search(rf"<{tag}(?=[\s>/])[^>]*>", masked, re.IGNORECASE)
        if opening:
            return f"{html[:opening.end()]}{image}{html[opening.end():]}", [f"inserted the brief logo {reference} at the start of <{tag}>"]
    body = re.search(r"<body\b[^>]*>", masked, re.IGNORECASE)
    if body is None:
        return html, []
    return f"{html[:body.end()]}<header>{image}</header>{html[body.end():]}", [f"inserted the brief logo {reference} in a new <header>"]
