import logging
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser

from wra.core.models import FeatureModel

logger = logging.getLogger(__name__)

_SPLIT_RE = re.compile(r"[\s·•|,()\[\]{}<>\"'`!?;:*=]+")
_TRIM_RE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)
_SIMILARITY_THRESHOLD = 0.85
# Typographic variants of characters an author never chose deliberately.
# Without this, a non-breaking hyphen rendered as a plain one reads as damage.
_CONFUSABLES = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", " ": " ", " ": " ",
})


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.text_parts.append(data)


def _tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _SPLIT_RE.split(unicodedata.normalize("NFKC", text).translate(_CONFUSABLES)):
        token = _TRIM_RE.sub("", raw)
        if token:
            tokens.append(token)
    return tokens


# Endings that make a Korean token a conjugated predicate rather than a name.
# `있습니다`, `드리겠습니다` and `공급하여` are sentences, not identities, and at
# the higher freedom levels the page is meant to be rewritten into different
# ones. Treating them as identifiers turned ordinary authorship into reported
# corruption, and the content gate turned that into a run-ending score of 4.
_KOREAN_PREDICATE_ENDINGS = (
    "습니다", "합니다", "입니다", "됩니다", "십니다",
    "했다", "한다", "된다", "이다", "였다", "있다", "없다",
    "하여", "하고", "하며", "해서", "하는", "드립니다", "세요",
)


def _is_korean_predicate(token: str) -> bool:
    return any(_is_hangul(character) for character in token) and token.endswith(_KOREAN_PREDICATE_ENDINGS)


def _is_identifier_like(token: str) -> bool:
    """Tokens where a one-character difference means corruption, not rewording.

    Prose may legitimately be reworded at the higher freedom levels, so only
    tokens that carry an identity are considered: codes and numbers, uppercase
    names, and CJK words that are not conjugated predicates.
    """
    if not token.isascii():
        return len(token) >= 4 and not _is_korean_predicate(token)
    if len(token) < 5:
        return False
    letters = [character for character in token if character.isalpha()]
    return any(character.isdigit() for character in token) or (len(letters) >= 2 and token.upper() == token)


# Korean attaches particles and verb endings straight onto the stem, so a
# rewritten sentence changes a token's tail while the identity in front of it
# survives intact. Those tails are grammar, not damage. Only closed-class
# fragments belong here: anything that could carry meaning of its own would
# excuse a real corruption.
_KOREAN_TAILS = frozenset({
    "", "은", "는", "이", "가", "을", "를", "과", "와", "의", "도", "만", "에", "께",
    "에서", "에게", "한테", "으로", "로", "까지", "부터", "보다", "처럼", "이나", "나",
    "고", "게", "며", "여", "면", "서", "지", "야", "요", "죠",
    "시고", "시며", "시여", "시는", "신", "하고", "하며", "하여", "하게", "하면",
    "합니다", "입니다", "습니다", "됩니다", "있고", "있게",
})
_MINIMUM_PRESERVED_STEM = 2


def _is_hangul(character: str) -> bool:
    return "가" <= character <= "힣"


def _is_korean_inflection(candidate: str, source: str) -> bool:
    """Whether two tokens are the same stem carrying different Korean grammar.

    The stem in front must survive untouched and be long enough to identify
    what is being said; only the trailing grammatical fragment may differ.
    `대성정밀은` and `대성정밀의` are one name inflected two ways, while
    `가능합니다` and `기능합니다` share no stem and mean different things.
    """
    prefix = 0
    for left, right in zip(candidate, source):
        if left != right:
            break
        prefix += 1
    if prefix < _MINIMUM_PRESERVED_STEM:
        return False
    tails = (candidate[prefix:], source[prefix:])
    if not all(all(_is_hangul(character) for character in tail) for tail in tails):
        return False
    return all(tail in _KOREAN_TAILS for tail in tails)


def _differs_by_one_character(candidate: str, source: str) -> bool:
    if len(candidate) != len(source):
        return False
    differences = sum(1 for left, right in zip(candidate, source) if left != right)
    return differences == 1


def _source_tokens(features: FeatureModel) -> set[str]:
    tokens: set[str] = set()

    def visit(value) -> None:
        if isinstance(value, str):
            tokens.update(token for token in _tokenize(value) if _is_identifier_like(token))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(features.model_dump(exclude_none=True))
    return tokens


def find_corrupted_tokens(html: str, features: FeatureModel) -> list[str]:
    """Report output tokens that are near-misses of a Feature Model identifier.

    Content freedom governs whether a source token may be dropped or replaced,
    never whether it may be restated incorrectly. A token that is absent is
    therefore fine; a token that appears in a slightly different form is not.
    """
    source = _source_tokens(features)
    if not source:
        return []

    parser = _TextParser()
    parser.feed(html)
    candidates = {token for token in _tokenize(" ".join(parser.text_parts)) if _is_identifier_like(token)}

    findings: list[str] = []
    for candidate in sorted(candidates - source):
        for original in source:
            if _is_korean_inflection(candidate, original):
                continue
            if original in candidate:
                # `드리겠습니다` inside `답변드리겠습니다`: every character of the
                # source survives, in order and unbroken. That is a longer word
                # built around the original, not a damaged copy of it. The
                # reverse is not safe -- a candidate inside a longer source has
                # lost characters, which is exactly what corruption looks like.
                continue
            if _differs_by_one_character(candidate, original) or SequenceMatcher(None, candidate, original).ratio() >= _SIMILARITY_THRESHOLD:
                findings.append(f"corrupted source token: {original!r} rendered as {candidate!r}")
                break

    if findings:
        logger.warning("Corrupted source tokens detected: %s", findings)
    return findings
