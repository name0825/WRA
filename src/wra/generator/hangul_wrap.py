import re

from .fixed_trap_repair import _inject_style

_MARKER = 'data-wra-repair="hangul-wrap"'
_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
_HANGUL = re.compile("[가-힣]")
_TAG = re.compile(r"<[^>]*>")
_CSS = ":where(h1,h2,h3,h4,h5,h6){word-break:keep-all;overflow-wrap:break-word}"


def _has_hangul_heading(html: str) -> bool:
    return any(_HANGUL.search(_TAG.sub("", match.group(2))) for match in _HEADING.finditer(html))


def repair_hangul_heading_wrap(html: str) -> tuple[str, list[str]]:
    """Keep Hangul words intact when headings wrap.

    Browsers default to `word-break: normal`, which may break between any two
    Hangul syllables. A zero-specificity `keep-all` rule on headings breaks at
    spaces only, while `overflow-wrap: break-word` still splits a single word
    that is wider than its container. Author rules that set `word-break`
    explicitly keep winning because `:where()` adds no specificity.
    """
    if _MARKER in html or not _has_hangul_heading(html):
        return html, []
    return _inject_style(html, _CSS, "hangul-wrap"), ["added word-break: keep-all to Hangul headings"]
