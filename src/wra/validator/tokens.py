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


def _is_identifier_like(token: str) -> bool:
    """Tokens where a one-character difference means corruption, not rewording.

    Prose may legitimately be reworded at the higher freedom levels, so only
    tokens that carry an identity are considered: codes and numbers, uppercase
    names, and CJK words.
    """
    if not token.isascii():
        return len(token) >= 4
    if len(token) < 5:
        return False
    letters = [character for character in token if character.isalpha()]
    return any(character.isdigit() for character in token) or (len(letters) >= 2 and token.upper() == token)


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
            if _differs_by_one_character(candidate, original) or SequenceMatcher(None, candidate, original).ratio() >= _SIMILARITY_THRESHOLD:
                findings.append(f"corrupted source token: {original!r} rendered as {candidate!r}")
                break

    if findings:
        logger.warning("Corrupted source tokens detected: %s", findings)
    return findings
