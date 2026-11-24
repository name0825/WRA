import re
import unicodedata
from html import escape, unescape
from urllib.parse import unquote

from wra.validator.dates import find_dropped_dates
from wra.validator.tokens import extract_visible_text

MISSING_BRIEF_FACT_PREFIX = "brief fact missing from page"

_MAX_FINDINGS = 12
_MAX_ENTRY_LINES = 6
_MAX_SOURCE_CHARS = 400
_ENTRY_RE = re.compile(r"^(\s*)-\s+\S")
_FACT_FINDING_RE = re.compile(rf"^{re.escape(MISSING_BRIEF_FACT_PREFIX)}: (date|url|item) (.+)$")
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@$&+,;=%!()]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!*)"
_ATTR_RE = re.compile(r"""\b(?:alt|title|aria-label|href)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d{1,3}[.)])\s+(.+?)\s*$")
_FLOW_LIST_RE = re.compile(r":\s*\[([^\[\]\n]*)\]\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_KEY_RE = re.compile(r"^\s*([A-Za-z_][\w-]*):(?:\s|$)")
_DIRECTIVE_KEYS = frozenset({"nav", "tone", "mood", "layout", "must", "avoid", "palette", "typography", "style", "rules", "constraints"})
_DIRECTIVE_HEADING_WORDS = ("디자인", "design", "스타일", "style", "분위기", "요구", "지침", "규칙", "제약")
_SPACED_DATE_RE = re.compile(r"(?<![\d.])(20\d{2})\.\s+(\d{1,2})(?!\d)\.?")
_NAME_ITEM_RE = re.compile(r"^\s*-\s+name:\s")
_LABEL_RE = re.compile(r"^[^:\[\]]{1,24}:\s+(.+)$")
_INVISIBLE_KEYS = ("id", "href", "url", "src", "link", "github", "label")
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9+#.\-]*")
_DATE_SPELLING_RE = re.compile(r"'([^']+)' is in the source")
_STOPWORDS = frozenset({
    "and", "the", "using", "use", "with", "for", "of", "to", "in", "on", "an", "or", "is", "are", "by", "from", "as", "at", "be",
})


def _squash(text: str) -> str:
    return re.sub(r"[\s\-]+", "", unicodedata.normalize("NFKC", text).lower())


def _url_key(url: str) -> str:
    key = re.sub(r"^https?://(?:www\.)?", "", unquote(url.rstrip(_URL_TRAILING)), flags=re.IGNORECASE)
    return key.lower().rstrip("/")


def _attribute_text(html: str) -> str:
    return " ".join(unquote(unescape(first or second)) for first, second in _ATTR_RE.findall(html))


def _is_directive(context: str) -> bool:
    lowered = context.lower()
    return lowered in _DIRECTIVE_KEYS or any(word in lowered for word in _DIRECTIVE_HEADING_WORDS)


def _is_group_label(lines: list[str], index: int) -> bool:
    line = lines[index]
    if not _NAME_ITEM_RE.match(line):
        return False
    column = len(line) - len(line.lstrip(" -"))
    for sibling in lines[index + 1:]:
        if not sibling.strip():
            continue
        indent = len(sibling) - len(sibling.lstrip(" "))
        if indent < column:
            return False
        if indent == column and re.match(r"items:(?:\s|$)", sibling.strip()):
            return True
    return False


def _list_items(brief: str) -> list[str]:
    items: list[str] = []
    levels = [len(match.group(1)) for match in _HEADING_RE.finditer(brief)]
    has_sections = any(level >= 2 for level in levels)
    context, level = "", 0
    lines = brief.splitlines()
    for index, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading:
            level, context = len(heading.group(1)), heading.group(2).strip()
            continue
        flow = _FLOW_LIST_RE.search(line)
        if flow:
            key = line.split(":", 1)[0].strip().lower()
            if not _is_directive(key) and not (has_sections and level < 2):
                items.extend(member.strip() for member in flow.group(1).split(",") if member.strip())
            continue
        match = _LIST_ITEM_RE.match(line)
        if not match:
            key = _KEY_RE.match(line)
            if key:
                context = key.group(1)
            continue
        if _is_directive(context) or (has_sections and level < 2) or _is_group_label(lines, index):
            continue
        item = match.group(1)
        labelled = _LABEL_RE.match(item)
        if labelled:
            if item.split(":", 1)[0].strip().lower() in _INVISIBLE_KEYS:
                continue
            item = labelled.group(1)
        items.append(item)
    return items


def _tokens(item: str) -> list[str]:
    stripped = _PHONE_RE.sub(" ", _EMAIL_RE.sub(" ", _URL_RE.sub(" ", item)))
    tokens = []
    for raw in _TOKEN_RE.findall(stripped):
        token = raw.rstrip(".-")
        if len(token) >= 2 and token.lower() not in _STOPWORDS:
            tokens.append(token)
    return tokens


def _token_on_page(token: str, page: str) -> bool:
    needle = _squash(token)
    if re.fullmatch(r"[가-힣]+", token) and len(needle) >= 3:
        needle = needle[:-1]
    return needle in page


def find_dropped_brief_facts(html: str, brief: str, limit: int = _MAX_FINDINGS) -> list[str]:
    """Dates, URLs and list items the brief states that the page no longer carries.

    A list item counts as dropped only when none of its key words (Latin
    identifiers, Hangul words) is on the page, so a reworded item is tolerated.
    Phones and emails belong to `find_missing_business_facts`.
    """
    if not brief or not brief.strip():
        return []
    findings: list[str] = []

    page_html = _SPACED_DATE_RE.sub(r"\1.\2", html)
    brief_html = "<p>" + escape(_SPACED_DATE_RE.sub(r"\1.\2", brief)) + "</p>"
    for finding in find_dropped_dates(page_html, brief_html):
        spelling = _DATE_SPELLING_RE.search(finding)
        findings.append(f"{MISSING_BRIEF_FACT_PREFIX}: date {spelling.group(1) if spelling else finding}")

    raw_html = unquote(unescape(html)).lower()
    seen_urls: set[str] = set()
    for url in _URL_RE.findall(brief):
        key = _url_key(url)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        if key not in raw_html:
            findings.append(f"{MISSING_BRIEF_FACT_PREFIX}: url {url.rstrip(_URL_TRAILING)}")

    page = _squash(extract_visible_text(html) + " " + _attribute_text(html))
    seen_items: set[str] = set()
    for item in _list_items(brief):
        tokens = _tokens(item)
        if not tokens:
            continue
        key = _squash(item)
        if key in seen_items:
            continue
        seen_items.add(key)
        if not any(_token_on_page(token, page) for token in tokens):
            findings.append(f"{MISSING_BRIEF_FACT_PREFIX}: item {item.strip()}")

    return findings[:limit]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _entry_lines(lines: list[str], index: int) -> list[str]:
    start = index
    if not _ENTRY_RE.match(lines[index]):
        for back in range(index - 1, max(index - 4, -1), -1):
            entry = _ENTRY_RE.match(lines[back])
            if entry and len(entry.group(1)) < _indent(lines[index]):
                start = back
                break
            if not lines[back].strip() or _indent(lines[back]) < _indent(lines[index]):
                break
    entry = _ENTRY_RE.match(lines[start])
    if entry is None:
        return [lines[index].strip()]
    end = start + 1
    while end < len(lines) and end - start < _MAX_ENTRY_LINES and lines[end].strip() and _indent(lines[end]) > len(entry.group(1)):
        end += 1
    return [line.strip() for line in lines[start:end]]


def locate_brief_facts(brief: str, findings: list[str]) -> list[dict]:
    """Each dropped brief fact with the brief entry that states it, so the entry can be restored whole."""
    lines = brief.splitlines()
    normalised = [_SPACED_DATE_RE.sub(r"\1.\2", line) for line in lines]
    located: list[dict] = []
    for finding in findings:
        match = _FACT_FINDING_RE.match(finding)
        if match is None:
            continue
        fact = match.group(2).strip()
        fact_entry = {"fact": fact}
        for index, line in enumerate(lines):
            if fact in line or fact in normalised[index]:
                fact_entry["source_line"] = " | ".join(_entry_lines(lines, index))[:_MAX_SOURCE_CHARS]
                break
        located.append(fact_entry)
    return located
