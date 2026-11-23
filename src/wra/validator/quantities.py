import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from wra.validator.dates import without_dates
from wra.validator.tokens import extract_visible_text

logger = logging.getLogger(__name__)

DROPPED_QUANTITY_PREFIX = "dropped source quantity"
FAQ_QUESTION_PREFIX = "dropped source question"

QuantityKey = tuple[str, str]

_UNITS = (
    "kg", "g", "mg", "lm", "lx", "W", "kW", "Wh", "mAh", "V", "A", "K", "mm", "cm", "m", "km", "ml", "mL", "L",
    "°C", "°F", "°", "%", "Hz", "GB", "TB", "MB", "원", "만원", "개", "명", "분", "시간", "년", "개월", "인치", "inch",
)
_UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted(_UNITS, key=len, reverse=True))
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_QUANTITY_RE = re.compile(
    rf"(?<![A-Za-z0-9_.,])(?<!\d-)(?P<first>{_NUMBER})(?:\s*[-–—~]\s*(?P<second>{_NUMBER}))?\s?(?P<unit>{_UNIT_PATTERN})(?![A-Za-z])"
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_SINGLE_LETTER_UNITS = frozenset({"g", "m", "V", "A", "K", "W", "L"})
_PARTICLES = "이가을를은는의에와과도만씩부까간당내미초짜대째으로입및정"


def _number_key(number: str) -> str:
    digits = number.replace(",", "")
    try:
        return format(Decimal(digits).normalize(), "f")
    except InvalidOperation:
        return digits


def _unit_key(unit: str) -> str:
    if unit.startswith("°"):
        return "°"
    return "mL" if unit == "ml" else unit


def _is_false_unit(text: str, match: re.Match[str]) -> bool:
    unit = match.group("unit")
    before = text[: match.start()]
    following = text[match.end() : match.end() + 1]
    if "가" <= following <= "힣" and following not in _PARTICLES:
        return True
    if unit == "분" and re.search(r"(?<![가-힣])\d+\s*시\s*$", before):
        return True
    gap = match.group(0)[: -len(unit)]
    return unit in _SINGLE_LETTER_UNITS and gap[-1:].isspace() and re.search(r"[A-Za-z]\s+$", before) is not None


def _quantities(text: str, strict: bool = False) -> list[tuple[QuantityKey, str]]:
    found = []
    joined = " ".join(without_dates(text).split())
    for match in _QUANTITY_RE.finditer(joined):
        unit = match.group("unit")
        if strict and _is_false_unit(joined, match):
            continue
        spelling = " ".join(match.group(0).split())
        for number in (match.group("first"), match.group("second")):
            if number is None or (unit == "년" and _YEAR_RE.fullmatch(number)):
                continue
            found.append(((_number_key(number), _unit_key(unit)), spelling))
    return found


def find_dropped_quantities(html: str, source_html: str) -> list[str]:
    """Source number-plus-unit quantities the page no longer carries, compared by (number, unit) rather than spelling.

    Each distinct source quantity needs at least one page match, so a source that
    repeats a quantity (a hidden duplicate menu) is not held to repeating it.
    """
    if not source_html or not source_html.strip():
        return []
    spellings: dict[QuantityKey, str] = {}
    for key, spelling in _quantities(extract_visible_text(source_html), strict=True):
        spellings.setdefault(key, spelling)
    if not spellings:
        return []
    present = {key for key, _ in _quantities(extract_visible_text(html))}

    findings = [
        f"{DROPPED_QUANTITY_PREFIX}: '{spelling}' is in the source but not on the page"
        for key, spelling in spellings.items()
        if key not in present
    ]
    if findings:
        logger.warning("Dropped source quantities detected: %s", findings)
    return findings


_QUESTION_TAGS = frozenset({"summary", "dt", "h2", "h3", "h4", "h5", "h6", "button"})
_SKIPPED_TAGS = frozenset({"script", "style", "template", "noscript"})
_QUESTION_END = ("?", "？")
_SUMMARY_ENDINGS = ("요", "까")
_QUESTION_CLASS_RE = re.compile(r"(?:^|[-_])question$", re.IGNORECASE)
_MAX_CLASSED_QUESTION_CHARS = 200
_IMPLICITLY_CLOSED_TAGS = frozenset({"p", "li"})
_VOID_TAGS =frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})


def _has_question_class(attrs: list[tuple[str, str | None]]) -> bool:
    return any(name == "class" and any(_QUESTION_CLASS_RE.search(token) for token in (value or "").split()) for name, value in attrs)


class _QuestionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.questions: list[str] = []
        self._active: str | None = None
        self._classed = False
        self._depth = 0
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("dt", "dd") and self._active == "dt":
            self._finalize()
        if self._classed and tag == self._active and tag in _IMPLICITLY_CLOSED_TAGS:
            self._finalize()
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif self._active is None and not self._skip_depth and (tag in _QUESTION_TAGS or (tag not in _VOID_TAGS and _has_question_class(attrs))):
            self._active = tag
            self._classed = tag not in _QUESTION_TAGS
            self._depth = 1
            self._parts = []
        elif self._classed and tag in _QUESTION_TAGS and not self._skip_depth:
            self._active = tag
            self._classed = False
            self._depth = 1
            self._parts = []
        elif self._classed and tag == self._active:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._classed and tag == self._active:
            self._depth -= 1
            if self._depth == 0:
                self._finalize()
        elif tag == self._active or (tag == "dl" and self._active == "dt"):
            self._finalize()

    def close(self) -> None:
        super().close()
        if self._active is not None:
            self._finalize()

    def _finalize(self) -> None:
        text = " ".join(unicodedata.normalize("NFKC", " ".join(self._parts)).split())
        if self._classed:
            accepted = bool(text) and len(text) <= _MAX_CLASSED_QUESTION_CHARS and (text.endswith(_QUESTION_END) or ("?" not in text and text.endswith(_SUMMARY_ENDINGS)))
        else:
            accepted = bool(text) and (text.endswith(_QUESTION_END) or (self._active == "summary" and text.endswith(_SUMMARY_ENDINGS)))
        if accepted:
            self.questions.append(text)
        self._active = None
        self._classed = False
        self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._active is not None and not self._skip_depth:
            self._parts.append(data)


def _question_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


_QUESTION_SUFFIXES = tuple(sorted((
    "에서도", "에서는", "에서", "으로", "하나요", "한가요", "인가요", "되나요", "입니까", "습니까", "는데",
    "나요", "가요", "까요", "지", "까", "요", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "할", "하",
), key=len, reverse=True))
_QUESTION_STOPWORDS = frozenset({"어떻게", "얼마나", "무엇", "어떤", "어디", "언제", "가능", "가능한", "있나요", "되나요", "하나요", "the", "does", "this", "that", "what", "how", "can", "you", "your", "for", "are", "with", "there"})
_TERM_RE = re.compile(r"[가-힣]+|[a-z]+|\d+")
_PAGE_QUESTION_RE = re.compile(r"[^.!?？\n]*[?？]")
_PAGE_QUESTION_CHARS = 120


def _question_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in _TERM_RE.findall(unicodedata.normalize("NFKC", text).casefold()):
        if token.isascii():
            term = token[:5]
        else:
            term = next((token[:-len(suffix)] for suffix in _QUESTION_SUFFIXES if token.endswith(suffix) and len(token) > len(suffix)), token)
        if term not in _QUESTION_STOPWORDS and token not in _QUESTION_STOPWORDS and (len(term) >= (3 if term.isascii() else 2) or term.isdigit()) and term not in terms:
            terms.append(term)
    return terms


def _page_questions(html: str) -> list[str]:
    parser = _QuestionParser()
    parser.feed(html)
    parser.close()
    spoken = [match[-_PAGE_QUESTION_CHARS:] for match in _PAGE_QUESTION_RE.findall(unicodedata.normalize("NFKC", extract_visible_text(html)))]
    return [_TERM_RE.findall(unicodedata.normalize("NFKC", text).casefold()) for text in parser.questions + spoken]


def _is_reworded_on_page(question: str, page_questions: list[list[str]]) -> bool:
    terms = _question_terms(question)
    if not terms:
        return False
    needed = max(1, len(terms) // 2)
    return any(sum(any(token.startswith(term) for token in page_question) for term in terms) >= needed for page_question in page_questions)


def find_dropped_questions(html: str, source_html: str, reworded_ok: bool = False) -> list[str]:
    """Source FAQ questions (details summaries, question headings, terms, buttons) the page no longer carries.

    Compared case-, whitespace- and punctuation-insensitively; each distinct
    source question needs to appear somewhere in the page's visible text.
    With reworded_ok, a question also counts as kept when a question on the
    page shares at least half of its key terms.
    """
    if not source_html or not source_html.strip():
        return []
    parser = _QuestionParser()
    parser.feed(source_html)
    parser.close()
    spellings: dict[str, str] = {}
    for question in parser.questions:
        key = _question_key(question)
        if key:
            spellings.setdefault(key, question)
    if not spellings:
        return []
    page_text = _question_key(extract_visible_text(html))
    page_questions = _page_questions(html) if reworded_ok else []

    findings = [
        f"{FAQ_QUESTION_PREFIX}: '{spelling}' is in the source but not on the page"
        for key, spelling in spellings.items()
        if key not in page_text and not (reworded_ok and _is_reworded_on_page(spelling, page_questions))
    ]
    if findings:
        logger.warning("Dropped source questions detected: %s", findings)
    return findings
