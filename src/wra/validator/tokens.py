import html as html_lib
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from html.parser import HTMLParser

from wra.core.models import ContentFreedom, FeatureModel

logger = logging.getLogger(__name__)

_SPLIT_RE = re.compile(r"(?:[\s·•|()\[\]{}<>\"'`!?;:*=]|(?<!\d),|,(?!\d))+")
_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿­]")
_TRIM_RE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)
_SIMILARITY_THRESHOLD = 0.85
# Typographic variants of characters an author never chose deliberately.
# Without this, a non-breaking hyphen rendered as a plain one reads as damage.
_CONFUSABLES = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", " ": " ", " ": " ",
})


_DEFAULT_SKIP_TAGS = frozenset({"script", "style"})


class _TextParser(HTMLParser):
    def __init__(self, skip_tags: frozenset[str] = _DEFAULT_SKIP_TAGS) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._skip_tags = skip_tags
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.text_parts.append(data)


_VISIBLE_TEXT_SKIP_TAGS = frozenset({"script", "style", "template", "noscript"})


def extract_visible_text(html: str, separator: str = " ") -> str:
    """The page's visible text: `script`/`style`/`template`/`noscript` content excluded, joined with `separator`."""
    parser = _TextParser(skip_tags=_VISIBLE_TEXT_SKIP_TAGS)
    parser.feed(html)
    return separator.join(parser.text_parts)


def normalize_for_match(text: str) -> str:
    """Collapse `text` to a whitespace-free, confusable-normalized form for exact-content comparison.

    NFKC-normalizes and translates typographic confusables the same way
    `_tokenize` does, then removes all whitespace so differences in how text
    nodes are joined or spaced (e.g. across an inline tag boundary) don't
    register as content changes.
    """
    normalized = _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", text)).translate(_CONFUSABLES)
    return re.sub(r"\s+", "", normalized)


def _tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _SPLIT_RE.split(_ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", text)).translate(_CONFUSABLES)):
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
_KOREAN_PARTICLES = frozenset({
    "", "은", "는", "이", "가", "을", "를", "과", "와", "의", "도", "만", "에", "께",
    "에서", "에게", "한테", "으로", "로", "까지", "부터", "보다", "처럼", "이나", "나",
})
_KOREAN_DERIVATIONAL_TAILS = frozenset({
    "한", "하는", "할", "해", "된", "되는", "될", "되어", "돼", "되고", "되며", "되게", "되면",
})
_MINIMUM_PRESERVED_STEM = 2
_PREDICATE_REWRITE_LEVELS = frozenset({"ultra", "madmax"})
_PREDICATE_FOLLOWER_PREFIXES = (
    "드리", "드립", "드릴", "드려", "드렸", "줍니", "주세", "주십", "주시", "준다", "줬", "줘",
    "보세", "보십", "보시", "봅니", "있", "없",
)
_PREDICATE_FOLLOWER_WORDS = frozenset({"수", "것", "거", "준", "줄", "주고", "주는", "주어", "보고", "보는", "볼"})


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
    if not all(tail in _KOREAN_TAILS or tail in _KOREAN_DERIVATIONAL_TAILS for tail in tails):
        return False
    derivational = [tail in _KOREAN_DERIVATIONAL_TAILS for tail in tails]
    if all(derivational):
        return False
    if any(derivational):
        if derivational[0]:
            return tails[1] in _KOREAN_PARTICLES
        return tails[0] in _KOREAN_PARTICLES and tails[0] != ""
    return all(tail in _KOREAN_TAILS for tail in tails)


def _differs_by_one_character(candidate: str, source: str) -> bool:
    if len(candidate) != len(source):
        return False
    differences = sum(1 for left, right in zip(candidate, source) if left != right)
    return differences == 1


def _differs_by_one_hangul_character(candidate: str, source: str) -> bool:
    if not _differs_by_one_character(candidate, source):
        return False
    for index, (left, right) in enumerate(zip(candidate, source)):
        if left != right:
            # A swapped final syllable is usually a different ending (`문의하면`
            # for `문의하기`), not a damaged word; restoring it breaks the sentence.
            return index < len(source) - 1 and _is_hangul(left) and _is_hangul(right)
    return False


def _is_rewritten_verb_ending(candidate: str, source: str) -> bool:
    if not _differs_by_one_character(candidate, source):
        return False
    return (
        source[-2:-1] in ("하", "되")
        and candidate[:-1] == source[:-1]
        and candidate[-1] in _PREDICATE_ENDING_SYLLABLES
        and source[-1] in _PREDICATE_ENDING_SYLLABLES
    )


_PREDICATE_ENDING_SYLLABLES = frozenset("기다면고여요며니서")
_THOUSANDS_GROUP_RE = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])")
_DATE_LIKE_RE = re.compile(r"\d{4}[./]\d{1,2}(?:[./]\d{1,2})?")
_TRAILING_GENERIC_UNITS = frozenset("팀부과처")


def _is_numeric_reformat(candidate: str, source: str) -> bool:
    left = _THOUSANDS_GROUP_RE.sub(lambda match: match.group(0).replace(",", ""), candidate)
    right = _THOUSANDS_GROUP_RE.sub(lambda match: match.group(0).replace(",", ""), source)
    if left == right:
        return True
    if not (_DATE_LIKE_RE.fullmatch(left) and _DATE_LIKE_RE.fullmatch(right)):
        return False
    if re.sub(r"\d+", "#", left) != re.sub(r"\d+", "#", right):
        return False
    return [int(run) for run in re.findall(r"\d+", left)] == [int(run) for run in re.findall(r"\d+", right)]


def _drops_trailing_generic_unit(candidate: str, source: str) -> bool:
    return (
        len(source) == len(candidate) + 1
        and source.startswith(candidate)
        and source[-1] in _TRAILING_GENERIC_UNITS
        and all(_is_hangul(character) for character in source)
    )


def _source_dump(features: FeatureModel) -> dict:
    dump = features.model_dump(exclude_none=True)
    site = dump.get("site")
    if isinstance(site, dict):
        site.pop("type", None)
        site.pop("industry", None)
        brand = site.get("brand")
        if isinstance(brand, dict):
            brand.pop("voice", None)
    return dump


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

    visit(_source_dump(features))
    return tokens


# The institution keyword plus the single word immediately before it, e.g.
# "Yeungnam University"; and the Korean equivalent, a word ending in a school
# suffix. Only one preceding word is captured -- a longer greedy span picks up
# unrelated words that merely happen to sit nearby in flattened source text or
# across text-node/tag boundaries on the page.
_INSTITUTION_EN_RE = re.compile(r"\b([A-Za-z][A-Za-z&.'-]*)\s+(University|College|Institute|School|Academy)\b")
_INSTITUTION_KO_RE = re.compile(r"[가-힣]{1,10}(?:대학교|대학|학교|연구소)")
_WHITESPACE_RE = re.compile(r"\s+")
_GENERIC_INSTITUTION_WORDS = ("기술", "부설", "기업", "연구", "종합", "부속", "자체", "자사")


def _is_generic_institution(core: str) -> bool:
    if not core.endswith("연구소"):
        return False
    stem =_INSTITUTION_SUFFIX_RE.sub("", core)
    while stem:
        for word in _GENERIC_INSTITUTION_WORDS:
            if stem.startswith(word):
                stem = stem[len(word):]
                break
        else:
            return False
    return True


def _extract_institution_cores(text: str) -> set[str]:
    """Institution cores found in `text`, never spanning a newline.

    `text` may be a single text node from the page or the whole flattened
    source; either way, a line break marks a boundary a core may not cross.
    """
    cores: set[str] = set()
    for line in text.splitlines():
        cores.update(f"{match.group(1)} {match.group(2)}" for match in _INSTITUTION_EN_RE.finditer(line))
        cores.update(core for core in _INSTITUTION_KO_RE.findall(line) if not _is_generic_institution(core))
    return cores


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def _normalize_institution_name(name: str) -> str:
    return _normalize_whitespace(name).lower()


_HANGUL_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_HANGUL_MEDIALS = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo",
    "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
]
_HANGUL_FINALS = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "l", "l",
    "m", "p", "p", "t", "t", "ng", "t", "ch", "k", "t", "p", "h",
]
_INSTITUTION_SUFFIX_RE = re.compile(r"(대학교|대학|학교|연구소)$")


def _romanize_hangul(text: str) -> str:
    parts = []
    for char in text:
        code = ord(char) - 0xAC00
        if 0 <= code < 11172:
            initial, remainder = divmod(code, 21 * 28)
            medial, final = divmod(remainder, 28)
            parts.append(_HANGUL_INITIALS[initial] + _HANGUL_MEDIALS[medial] + _HANGUL_FINALS[final])
        else:
            parts.append(char)
    return "".join(parts)


def _institution_skeleton(stem: str) -> str:
    """Consonant skeleton of an institution name stem, script-agnostic.

    Hangul stems are romanized first (Revised Romanization, syllable by
    syllable); Latin stems are used as-is. Both are then reduced to their
    bare consonant sequence so the same institution written in either script
    lands on the same skeleton.
    """
    romanized = _romanize_hangul(stem).lower()
    romanized = romanized.replace("ch", "j").replace("k", "g").replace("p", "b").replace("t", "d").replace("r", "l")
    consonants = [char for char in romanized if char.isalpha() and char not in "aeiouyw"]
    skeleton: list[str] = []
    for char in consonants:
        if not skeleton or skeleton[-1] != char:
            skeleton.append(char)
    return "".join(skeleton)


def _institution_core_stem(core: str) -> str:
    match = _INSTITUTION_EN_RE.fullmatch(core)
    if match:
        return match.group(1)
    return _INSTITUTION_SUFFIX_RE.sub("", core)


def _source_text_segments(features: FeatureModel) -> list[str]:
    texts: list[str] = []

    def visit(value) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(_source_dump(features))
    return texts


def all_source_text(features: FeatureModel) -> str:
    return " ".join(_source_text_segments(features))


def _find_institution_substitutions(html: str, features: FeatureModel) -> list[str]:
    """Report a source institution replaced by a different one, not merely dropped.

    Dropping the institution is allowed by content freedom; naming a different
    one is an identity error the near-miss check above cannot see, since the
    two names share no characters at all.
    """
    source_text = all_source_text(features)
    source_cores = _extract_institution_cores(source_text)
    if not source_cores:
        return []

    parser = _TextParser()
    parser.feed(html)
    page_cores: set[str] = set()
    for part in parser.text_parts:
        page_cores.update(_extract_institution_cores(part))

    normalized_page_text = _normalize_institution_name(" ".join(parser.text_parts))
    normalized_source_text = _normalize_institution_name(source_text)

    missing_source_cores = sorted(
        core for core in source_cores if _normalize_institution_name(core) not in normalized_page_text
    )
    unmatched_page_cores = sorted(
        core for core in page_cores if _normalize_institution_name(core) not in normalized_source_text
    )
    if not missing_source_cores or not unmatched_page_cores:
        return []

    def is_hangul_core(core: str) -> bool:
        return bool(_INSTITUTION_KO_RE.fullmatch(core))

    remaining_missing = []
    matched_page_cores: set[str] = set()
    for core in missing_source_cores:
        skeleton = _institution_skeleton(_institution_core_stem(core))
        matched = None
        if skeleton:
            for page_core in unmatched_page_cores:
                if is_hangul_core(page_core) == is_hangul_core(core):
                    continue
                if _institution_skeleton(_institution_core_stem(page_core)) == skeleton:
                    matched = page_core
                    break
        if matched:
            matched_page_cores.add(matched)
        else:
            remaining_missing.append(core)

    usable_page_cores = [core for core in unmatched_page_cores if core not in matched_page_cores]
    if not remaining_missing or not usable_page_cores:
        return []

    other = usable_page_cores[0]
    return [
        f'Institution "{core}" from the source is absent while the page names another institution ("{other}")'
        for core in remaining_missing
    ]


_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_HANGUL_RUN_RE = re.compile(r"[가-힣]+")
# Capped at 5 words so an unbounded phrase can never swallow an unrelated sentence.
_TRANSLATED_PHRASE_GROUP = r"([A-Za-z&'\-]+(?: [A-Za-z&'\-]+){0,4})"


def _flex_literal(text: str) -> str:
    """`re.escape(text)` with whitespace runs loosened to `\\s+`, so tag-boundary reflow doesn't break the match."""
    pieces = []
    for part in re.split(r"(\s+)", text):
        if not part:
            continue
        pieces.append(r"\s+" if part.isspace() else re.escape(part))
    return "".join(pieces)


@lru_cache(maxsize=8)
def _patterns_for_segments(segments: tuple[str, ...]) -> tuple[tuple[re.Pattern[str], str, str, str], ...]:
    """A `(pattern, hangul_run, segment, context_word)` quadruple per source Hangul run with Latin context.

    `pattern` matches the run's literal surrounding context (whitespace-flexible) with the
    run itself replaced by a group capturing a bounded (<=5 word) Latin-only phrase -- what a
    translated rendering of that run would look like in the output. Context is required to be
    non-empty on both sides so a run sitting at a segment's start or end -- where the other
    side alone would leave the phrase free to start mid-sentence -- never builds a pattern.
    `context_word` is a literal word from that context, letting a caller cheaply rule out a
    text node before running the full pattern against it. Results are cached per segment tuple
    since the same features are typically checked and repaired within one pipeline pass.
    """
    patterns: list[tuple[re.Pattern[str], str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for segment in segments:
        for run_match in _HANGUL_RUN_RE.finditer(segment):
            run = run_match.group(0)
            if len(run) <= 1:
                continue
            before, after = segment[: run_match.start()], segment[run_match.end() :]
            if not (before.strip() and after.strip()):
                continue
            latin_match = _LATIN_WORD_RE.search(before) or _LATIN_WORD_RE.search(after)
            if not latin_match:
                continue
            key = (before, run, after)
            if key in seen:
                continue
            seen.add(key)
            pattern = re.compile(_flex_literal(before) + _TRANSLATED_PHRASE_GROUP + _flex_literal(after))
            patterns.append((pattern, run, segment, latin_match.group(0)))
    return tuple(patterns)


def _translated_proper_noun_patterns(features: FeatureModel) -> tuple[tuple[re.Pattern[str], str, str, str], ...]:
    return _patterns_for_segments(tuple(_source_text_segments(features)))


def _find_translated_proper_nouns(html: str, features: FeatureModel) -> list[str]:
    """Report a source Hangul proper noun that was translated into an English phrase.

    Content freedom never licenses restating a name in a different language; a
    Hangul run with Latin context around it in the source, found translated to a
    Latin-only phrase in that same context in the output, is reported unless the
    untranslated segment also survives (both forms present) or the run itself is
    still part of the matched span.
    """
    patterns = _translated_proper_noun_patterns(features)
    if not patterns:
        return []

    visible_text = extract_visible_text(html)
    normalized_output = normalize_for_match(visible_text)
    findings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for pattern, run, segment, context_word in patterns:
        if context_word not in visible_text:
            continue
        match = pattern.search(visible_text)
        if not match:
            continue
        if run in match.group(0):
            continue
        if normalize_for_match(segment) in normalized_output:
            continue
        phrase = match.group(1)
        key = (run, phrase)
        if key in seen:
            continue
        seen.add(key)
        findings.append(f"translated proper noun: '{run}' rendered as '{phrase}'")
    return findings


class _PositionedTextParser(HTMLParser):
    """Like `_TextParser`, but groups each text node's pieces with their absolute offsets into the fed HTML.

    A "node" is the maximal run of data/entity/char references between tag, comment, or
    declaration boundaries -- skipping the same tags `extract_visible_text` does, so detection
    and repair agree on what counts as visible text. Positions come from `getpos()`, converted
    to an absolute index, so a caller can splice a replacement into the original string without
    touching tags, attributes, comments, or declarations.
    """

    def __init__(self, html: str, skip_tags: frozenset[str] = _VISIBLE_TEXT_SKIP_TAGS) -> None:
        super().__init__(convert_charrefs=False)
        self._line_starts = [0]
        index = html.find("\n")
        while index != -1:
            self._line_starts.append(index + 1)
            index = html.find("\n", index + 1)
        self._skip_tags = skip_tags
        self._skip_depth = 0
        self.nodes: list[list[tuple[int, str, bool]]] = []
        self._current: list[tuple[int, str, bool]] | None = None

    def _offset(self) -> int:
        lineno, col = self.getpos()
        return self._line_starts[lineno - 1] + col

    def _flush(self) -> None:
        if self._current:
            self.nodes.append(self._current)
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._flush()
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._flush()

    def handle_endtag(self, tag: str) -> None:
        self._flush()
        if tag in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1

    def handle_comment(self, data: str) -> None:
        self._flush()

    def handle_decl(self, decl: str) -> None:
        self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if self._current is None:
            self._current = []
        self._current.append((self._offset(), data, False))

    def _handle_ref(self, raw: str) -> None:
        if self._skip_depth:
            return
        if self._current is None:
            self._current = []
        self._current.append((self._offset(), raw, True))

    def handle_entityref(self, name: str) -> None:
        self._handle_ref(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._handle_ref(f"&#{name};")

    def close(self) -> None:
        super().close()
        self._flush()


def _decode_node(node: list[tuple[int, str, bool]]) -> tuple[str, list[tuple[int, int, int, bool]]]:
    """Decode a text node's pieces into one string, with metadata mapping back to raw offsets.

    Entities are decoded so matching sees the same text `extract_visible_text` does. Metadata
    is `(decoded_start, decoded_end, raw_start, is_entity)` per piece, so a match confined to a
    single non-entity piece can be spliced back into the raw HTML precisely.
    """
    parts: list[str] = []
    meta: list[tuple[int, int, int, bool]] = []
    pos = 0
    for raw_start, text, is_entity in node:
        decoded = html_lib.unescape(text) if is_entity else text
        meta.append((pos, pos + len(decoded), raw_start, is_entity))
        parts.append(decoded)
        pos += len(decoded)
    return "".join(parts), meta


def _raw_offset_for_span(meta: list[tuple[int, int, int, bool]], start: int, end: int) -> int | None:
    """The raw-HTML offset for a decoded-text span, or `None` if it crosses or lands on an entity."""
    for decoded_start, decoded_end, raw_start, is_entity in meta:
        if is_entity:
            continue
        if decoded_start <= start and end <= decoded_end:
            return raw_start + (start - decoded_start)
    return None


def repair_translated_proper_nouns(html: str, features: FeatureModel) -> tuple[str, list[str]]:
    """Rewrite a translated proper-noun phrase back to its source Hangul run, in text nodes only.

    Matching is scoped to each text node's own decoded data, the same tag scope and text
    `_find_translated_proper_nouns` reports against, so attributes, scripts, styles,
    templates, and noscript content are left untouched. A match that crosses or lands inside
    an HTML entity is skipped rather than spliced, since its raw and decoded forms differ.
    """
    patterns = _translated_proper_noun_patterns(features)
    if not patterns:
        return html, []

    parser = _PositionedTextParser(html)
    parser.feed(html)
    parser.close()

    applied: list[str] = []
    replacements: list[tuple[int, int, str]] = []
    for node in parser.nodes:
        decoded_text, meta = _decode_node(node)
        for pattern, run, _segment, context_word in patterns:
            if context_word not in decoded_text:
                continue
            for match in pattern.finditer(decoded_text):
                if run in match.group(0):
                    continue
                raw_start = _raw_offset_for_span(meta, match.start(), match.end())
                if raw_start is None:
                    continue
                rel_start, rel_end = match.start(1) - match.start(0), match.end(1) - match.start(0)
                new_text = match.group(0)[:rel_start] + run + match.group(0)[rel_end:]
                rewrite = f"'{match.group(1)}' -> '{run}'"
                if rewrite not in applied:
                    applied.append(rewrite)
                replacements.append((raw_start, raw_start + (match.end() - match.start()), new_text))

    if not replacements:
        return html, []

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, text in replacements:
        if start < cursor:
            continue
        pieces.append(html[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces), applied


_LEAKED_TOKEN_RE = re.compile(r"\bWRA[\s_.\-]*([A-Z]{1,4})[\s_.\-]*\d+(?![A-Za-z0-9])")
_MANGLED_NUMBER_RE = re.compile(r"^(\d{3,})([a-z]{1,3})$")
_DIGIT_RUN_RE = re.compile(r"\d+")
_UNIT_SUFFIXES = frozenset({
    "st", "nd", "rd", "th", "px", "em", "rem", "ms", "s", "m", "h", "k", "km", "kg", "g", "mb", "gb", "kb", "x",
    "vw", "vh", "pt", "yr", "yrs", "sec", "min", "hr", "hrs", "ea", "pcs", "fps", "hz", "dpi", "mm", "cm", "ml", "l",
})


def _find_leaked_placeholder_tokens(html: str) -> list[str]:
    """Report a `WRAREF`-style token that survived restoration unmangled *and* mangled.

    `restore()` recovers most mangled forms, but any token it cannot map back to a
    literal is left in the page; that is a leak, not a valid piece of content.
    """
    parser = _TextParser()
    parser.feed(html)
    visible_text = " ".join(parser.text_parts)
    tokens: dict[str, None] = {}
    for match in _LEAKED_TOKEN_RE.finditer(visible_text):
        if any(letter in ("R", "E", "F") for letter in match.group(1)):
            tokens.setdefault(match.group(0), None)
    return [f"leaked placeholder token: '{token}'" for token in tokens]


def _find_mangled_number_tokens(html: str, features: FeatureModel) -> list[str]:
    """Report a source number whose digits survived but picked up stray letters.

    A number followed by a unit or an ordinal suffix ("100ms", "2027th") is
    ordinary prose, not damage, so those suffixes are excused.
    """
    parser = _TextParser()
    parser.feed(html)
    visible_text = " ".join(parser.text_parts)
    source_text = all_source_text(features)
    source_runs = _DIGIT_RUN_RE.findall(source_text)

    findings: list[str] = []
    for token in _tokenize(visible_text):
        match = _MANGLED_NUMBER_RE.match(token)
        if not match:
            continue
        prefix, suffix = match.group(1), match.group(2)
        if suffix in _UNIT_SUFFIXES:
            continue
        if all(letter in "abcdef" for letter in suffix):
            continue
        if f"#{token}" in visible_text:
            continue
        if token in source_text:
            continue
        originals = sorted({run for run in source_runs if run.startswith(prefix) and len(run) > len(prefix)})
        if originals:
            findings.append(f"corrupted source token: {' or '.join(repr(run) for run in originals)} rendered as '{token}'")
    return findings


_KOREAN_NATIVE_NUMERALS = frozenset({
    "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "스무", "열두",
})
_NUMERAL_WORD_RE = re.compile(r"^\d+$")
_MULTIPLIER_TOKEN_RE = re.compile(r"^(\d+)(.+)$")
_MULTIPLIER_RE = r"(?:만|천|백|억|조)"


def _is_numeral_word(token: str) -> bool:
    return bool(_NUMERAL_WORD_RE.match(token)) or token in _KOREAN_NATIVE_NUMERALS


_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?。]+\s+")


def _dropped_numeral_context(features: FeatureModel) -> tuple[set[tuple[str, str]], set[str], dict[tuple[str, str], list[str]], str]:
    """Shared source-side lookups for both detecting and repairing dropped numerals.

    Word/numeral/word gaps are only built within one sentence: `all_source_text`
    flattens every source field into one string, so an unrelated pair straddling a
    sentence or section boundary (a citation year followed by the next heading) can
    line up as if it were a single phrase with the numeral removed. It never was one.
    """
    source_text = all_source_text(features)
    source_pairs: set[tuple[str, str]] = set()
    source_token_set: set[str] = set()
    numeral_gaps: dict[tuple[str, str], list[str]] = {}
    for sentence in _SENTENCE_BOUNDARY_RE.split(source_text):
        sentence_tokens = _tokenize(sentence)
        source_pairs.update(zip(sentence_tokens, sentence_tokens[1:]))
        source_token_set.update(sentence_tokens)
        for w1, numeral, w2 in zip(sentence_tokens, sentence_tokens[1:], sentence_tokens[2:]):
            if _is_numeral_word(numeral):
                numeral_gaps.setdefault((w1, w2), []).append(numeral)
    normalized_source = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", source_text))
    return source_pairs, source_token_set, numeral_gaps, normalized_source


def _find_dropped_numerals(html: str, features: FeatureModel) -> list[str]:
    """Report a source number phrase restated with its numeral or multiplier dropped.

    Content freedom permits leaving a number out of the page entirely, but not
    restating its surrounding words with the numeral itself silently deleted --
    a source "X num Y" surviving as "X Y", or a digit run's multiplier suffix
    dropped from between the digits and the word that followed it.
    """
    output_tokens = _tokenize(extract_visible_text(html))
    source_pairs, source_token_set, numeral_gaps, normalized_source = _dropped_numeral_context(features)

    findings: list[str] = []
    seen: set[str] = set()

    for w1, w2 in zip(output_tokens, output_tokens[1:]):
        if (w1, w2) in source_pairs or (w1, w2) not in numeral_gaps:
            continue
        finding = f"corrupted source token: '{w1} {numeral_gaps[(w1, w2)][0]} {w2}' rendered as '{w1} {w2}'"
        if finding not in seen:
            seen.add(finding)
            findings.append(finding)

    for token in output_tokens:
        match = _MULTIPLIER_TOKEN_RE.match(token)
        if not match or token in source_token_set:
            continue
        digits, rest = match.group(1), match.group(2)
        source_match = re.search(r"(?<!\d)" + re.escape(digits) + _MULTIPLIER_RE + r"\s*" + re.escape(rest), normalized_source)
        if not source_match:
            continue
        finding = f"corrupted source token: '{source_match.group(0)}' rendered as '{token}'"
        if finding not in seen:
            seen.add(finding)
            findings.append(finding)

    return findings


_TOKEN_SPAN_RE = re.compile(r"(?:[^\s·•|,()\[\]{}<>\"'`!?;:*=]|(?<=\d),(?=\d))+")
_TOKEN_LEAD_TRIM_RE = re.compile(r"^[^\w]+", re.UNICODE)
_TOKEN_TAIL_TRIM_RE = re.compile(r"[^\w]+$", re.UNICODE)


def _normalize_token_text(text: str) -> str:
    return _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", text)).translate(_CONFUSABLES)


def _positioned_tokens(decoded_text: str) -> list[tuple[str, int, int]]:
    """Decoded-text tokens as `(normalized_token, start, end)`, trimmed the same way `_tokenize` trims."""
    tokens: list[tuple[str, int, int]] = []
    for span in _TOKEN_SPAN_RE.finditer(decoded_text):
        raw, start, end = span.group(0), span.start(), span.end()
        lead = _TOKEN_LEAD_TRIM_RE.match(raw)
        start += len(lead.group(0)) if lead else 0
        tail = _TOKEN_TAIL_TRIM_RE.search(raw)
        end -= len(tail.group(0)) if tail else 0
        if start >= end:
            continue
        tokens.append((_normalize_token_text(decoded_text[start:end]), start, end))
    return tokens


def repair_dropped_numerals(html: str, features: FeatureModel) -> tuple[str, list[str]]:
    """Restore a numeral or multiplier `_find_dropped_numerals` reports as silently dropped.

    Repaired only when the source gives a single, unambiguous numeral or multiplier form for
    the gap -- an ambiguous source (two different numbers that would both fit) is left for the
    model to judge rather than guessed at. Matching is scoped to each text node's own decoded
    data, the same scope `_find_dropped_numerals` reports against, so attributes, scripts,
    styles, templates, and noscript content are left untouched.
    """
    source_pairs, source_token_set, numeral_gaps, normalized_source = _dropped_numeral_context(features)
    if not source_token_set:
        return html, []

    parser = _PositionedTextParser(html)
    parser.feed(html)
    parser.close()

    applied: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for node in parser.nodes:
        decoded_text, meta = _decode_node(node)
        tokens = _positioned_tokens(decoded_text)

        for token, start, end in tokens:
            match = _MULTIPLIER_TOKEN_RE.match(token)
            if not match or token in source_token_set:
                continue
            digits, rest = match.group(1), match.group(2)
            pattern = r"(?<!\d)" + re.escape(digits) + _MULTIPLIER_RE + r"\s*" + re.escape(rest)
            source_forms = {source_match.group(0) for source_match in re.finditer(pattern, normalized_source)}
            if len(source_forms) != 1:
                continue
            raw_start = _raw_offset_for_span(meta, start, end)
            if raw_start is None:
                continue
            source_form = next(iter(source_forms))
            rewrite = f"'{token}' -> '{source_form}'"
            if rewrite not in applied:
                applied.append(rewrite)
            replacements.append((raw_start, raw_start + (end - start), source_form))

        for (w1, s1, e1), (w2, _s2, _e2) in zip(tokens, tokens[1:]):
            if (w1, w2) in source_pairs:
                continue
            numerals = numeral_gaps.get((w1, w2))
            if not numerals or len(set(numerals)) != 1:
                continue
            raw_pos = _raw_offset_for_span(meta, e1, e1)
            if raw_pos is None:
                continue
            numeral = numerals[0]
            rewrite = f"'{w1} {w2}' -> '{w1} {numeral} {w2}'"
            if rewrite not in applied:
                applied.append(rewrite)
            replacements.append((raw_pos, raw_pos, f" {numeral}"))

    if not replacements:
        return html, []

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, text in replacements:
        if start < cursor:
            continue
        pieces.append(html[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces), applied


_HAN_KANA_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
_LATIN_AFTER_HANGUL_RE = re.compile(r"[가-힣][a-z]{2,}")


def _script_mixing_findings(html: str, features: FeatureModel) -> list[tuple[str, str]]:
    """Pair each leaked-script token with its finding text."""
    visible_text = extract_visible_text(html)
    source_text = all_source_text(features)
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()

    if any(_is_hangul(character) for character in source_text):
        for token in visible_text.split():
            offending = [character for character in dict.fromkeys(token) if _HAN_KANA_RE.match(character) and character not in source_text]
            if offending and token not in seen:
                seen.add(token)
                findings.append((token, f"foreign script in Korean text: '{token}' contains {''.join(offending)} not present in the source"))

    for raw in visible_text.split():
        token = _TRIM_RE.sub("", raw)
        if token in seen or not _LATIN_AFTER_HANGUL_RE.search(token):
            continue
        if token in source_text:
            continue
        seen.add(token)
        findings.append((token, f"foreign script in Korean text: '{token}' mixes Hangul and Latin letters not present in the source"))

    return findings


def find_script_mixing_tokens(html: str, features: FeatureModel) -> list[str]:
    """The distinct leaked-script tokens `_find_script_mixing` reports, for repair to act on."""
    return [token for token, _ in _script_mixing_findings(html, features)]


def _find_script_mixing(html: str, features: FeatureModel) -> list[str]:
    """Report Han/kana leaked into Korean text, or Latin letters glued after a Hangul syllable, that the source never wrote."""
    return [message for _, message in _script_mixing_findings(html, features)]


_PARTICLE_PAIRS = (("으로", "로"), ("은", "는"), ("이", "가"), ("을", "를"), ("과", "와"))


def _has_batchim(character: str, pair: tuple[str, str] | None = None) -> bool | None:
    if not _is_hangul(character):
        return None
    jongseong = (ord(character) - 0xAC00) % 28
    if pair == ("으로", "로") and jongseong == 8:
        # A rieul-final syllable (서울, 메뉴얼) takes 로, not 으로, unlike other batchim.
        return False
    return jongseong != 0


def _correct_particle(pair: tuple[str, str], has_batchim: bool) -> str:
    with_batchim, without_batchim = pair
    return with_batchim if has_batchim else without_batchim


def _raw_source_tokens(features: FeatureModel) -> set[str]:
    return set(_tokenize(all_source_text(features)))


def _find_particle_swaps(html: str, features: FeatureModel) -> list[str]:
    """Report a source stem restated with the wrong allomorph of its particle.

    A verb ending like `있는`/`먹는` has the same trailing syllable as a swapped
    topic particle, but its "corrected" counterpart (`있은`, `먹은`) is not a
    real word and never appears in the source, so it fails the anchor below and
    is never reported; only a stem whose correctly-paired form the source
    actually uses is.
    """
    source = _raw_source_tokens(features)
    if not source:
        return []

    findings: list[str] = []
    seen: set[str] = set()
    for token in _tokenize(extract_visible_text(html)):
        if token in source:
            continue
        for pair in _PARTICLE_PAIRS:
            matched = False
            for particle in pair:
                if not token.endswith(particle) or len(token) <= len(particle):
                    continue
                stem = token[: -len(particle)]
                has_batchim = _has_batchim(stem[-1], pair)
                if has_batchim is None:
                    continue
                correct = _correct_particle(pair, has_batchim)
                if particle == correct:
                    continue
                expected = stem + correct
                if expected in source:
                    finding = f"corrupted source token: '{expected}' rendered as '{token}'"
                    if finding not in seen:
                        seen.add(finding)
                        findings.append(finding)
                matched = True
                break
            if matched:
                break
    return findings


_LATIN_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "but", "to", "in", "on", "at", "for",
    "with", "as", "by", "is", "are", "was", "were", "be", "from", "that", "this",
})


def _is_latin_function_word(token: str) -> bool:
    return token.isascii() and token.isalpha() and token.lower() in _LATIN_FUNCTION_WORDS


def _digits_only_diff(candidate: str, source: str) -> bool:
    if len(candidate) != len(source) or candidate == source:
        return False
    for left, right in zip(candidate, source):
        if left == right:
            continue
        if not (left.isdigit() and right.isdigit()):
            return False
    return True


def _mid_token_context(features: FeatureModel) -> tuple[dict[tuple[str, str], set[str]], dict[str, set[str]], set[str]]:
    source_tokens = _tokenize(all_source_text(features))
    context: dict[tuple[str, str], set[str]] = {}
    prev_values: dict[str, set[str]] = {}
    for prev, mid, nxt in zip(source_tokens, source_tokens[1:], source_tokens[2:]):
        context.setdefault((prev, nxt), set()).add(mid)
        prev_values.setdefault(prev, set()).add(mid)
    return context, prev_values, set(source_tokens)


def _mid_token_triples(html: str) -> list[tuple[str, str, str, list[tuple[int, int, int, bool]], int, int]]:
    """Same-node (prev, mid, next) token triples, with `mid`'s span metadata for splicing repairs.

    A triple is only formed from tokens sharing one text node, so a prev/mid/next
    span never crosses a sibling tag boundary (e.g. `<p>총 <b>3개</b> 항목이 있습니다.</p>`),
    matching the scope `_raw_offset_for_span` can actually splice a repair into.
    """
    parser = _PositionedTextParser(html)
    parser.feed(html)
    parser.close()

    triples: list[tuple[str, str, str, list[tuple[int, int, int, bool]], int, int]] = []
    for node in parser.nodes:
        decoded_text, meta = _decode_node(node)
        tokens = _positioned_tokens(decoded_text)
        for (prev, _prev_start, _prev_end), (mid, mid_start, mid_end), (nxt, _nxt_start, _nxt_end) in zip(tokens, tokens[1:], tokens[2:]):
            triples.append((prev, mid, nxt, meta, mid_start, mid_end))
    return triples


def _mid_token_substitution(
    prev: str,
    mid: str,
    nxt: str,
    context: dict[tuple[str, str], set[str]],
    prev_values: dict[str, set[str]],
    source_vocab: set[str],
) -> str | None:
    """The source's middle token if `mid` looks like it was substituted in place, else `None`."""
    mids = context.get((prev, nxt))
    if not mids or mid in mids or len(mids) != 1 or mid in prev_values.get(prev, ()):
        return None
    expected = next(iter(mids))
    is_latin_swap = _is_latin_function_word(mid) and any(_is_hangul(character) for character in prev + nxt)
    if is_latin_swap or (_digits_only_diff(mid, expected) and mid not in source_vocab):
        return expected
    return None


def _find_mid_token_substitutions(html: str, features: FeatureModel) -> list[str]:
    """Report a single output token swapped in place with both neighbors intact.

    Rewording legitimately changes the words around a phrase, so a substitution
    is only reported when both the word before and the word after are exactly
    what the source has at that spot and the source's own middle word there is
    unambiguous -- and even then, only for the two shapes a rewording would
    never produce: a Latin function word dropped into Hangul prose, or a digit
    swapped for another digit at the same position. A repeated list item (a
    citation venue reused across entries, say) can make the same anchor word
    recur with a different neighbor; when the output's value there is itself
    one of that anchor's other legitimate values, it is a reordering artifact,
    not a corruption, and is left unreported. The triple is also confined to a
    single text node, the same scope the repair splices into, so a finding here
    is always one the pipeline can actually fix.
    """
    context, prev_values, source_vocab = _mid_token_context(features)
    if not context:
        return []

    findings: list[str] = []
    seen: set[str] = set()
    for prev, mid, nxt, _meta, _mid_start, _mid_end in _mid_token_triples(html):
        expected = _mid_token_substitution(prev, mid, nxt, context, prev_values, source_vocab)
        if expected is None:
            continue
        finding = f"corrupted source token: '{expected}' rendered as '{mid}'"
        if finding not in seen:
            seen.add(finding)
            findings.append(finding)
    return findings


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)


def _visible_email_matches(html: str) -> list[re.Match[str]]:
    """`_EMAIL_RE` matches outside `<script>`/`<style>` content, keeping mailto hrefs and visible text in scope."""
    spans = [(match.start(), match.end()) for match in _SCRIPT_STYLE_RE.finditer(html)]
    return [match for match in _EMAIL_RE.finditer(html) if not any(start <= match.start() < end for start, end in spans)]


def _find_email_substitutions(html: str, features: FeatureModel) -> list[str]:
    """Report an output email whose domain matches exactly one source email but whose local part does not.

    A domain shared with a single source address anchors the substitution; an
    output email on an unrelated domain is a new address, not a corruption.
    Scanned across the whole document, not just visible text, since a changed
    address most often surfaces in a `mailto:` href rather than the link text --
    but not inside `<script>`/`<style>` content, which isn't rendered at all.
    """
    source_emails = set(_EMAIL_RE.findall(all_source_text(features)))
    if not source_emails:
        return []

    findings: list[str] = []
    for email in sorted({match.group(0) for match in _visible_email_matches(html)} - source_emails):
        domain = email.split("@", 1)[1]
        matches = [source_email for source_email in source_emails if source_email.split("@", 1)[1] == domain]
        if len(matches) == 1:
            findings.append(f"corrupted source token: '{matches[0]}' rendered as '{email}'")
    return findings


def _is_digit_restatement(candidate: str, original: str, source_text: str) -> bool:
    """Whether `candidate` only restates a digit run `original` already carries, in the same role the source gives it.

    `08:00` restated as `오전 8시부터` is the same hour the source already
    states, in the same role -- immediately before the same character, or, for
    an hour, drawn from an `HH:MM` time. A digit that merely coincides with an
    unrelated number elsewhere in the source, such as an address number, does
    not count: that role has to match, not just the digit.
    """
    if _DIGIT_RUN_RE.sub("", candidate) != _DIGIT_RUN_RE.sub("", original):
        return False
    matched_any = False
    for match in _DIGIT_RUN_RE.finditer(candidate):
        matched_any = True
        value = int(match.group(0))
        following = candidate[match.end():match.end() + 1]
        if not following:
            return False
        if re.search(r"(?<!\d)0*" + str(value) + re.escape(following), source_text):
            continue
        if following == "시" and re.search(r"(?<!\d)0?" + str(value) + r":\d{2}", source_text):
            continue
        return False
    return matched_any


def _neighbour_map(tokens: list[str]) -> dict[str, tuple[set[str], set[str]]]:
    """token -> (predecessors, successors) immediately adjacent to every occurrence of that token in `tokens`."""
    neighbours: dict[str, tuple[set[str], set[str]]] = {}
    for index, token in enumerate(tokens):
        prevs, nexts = neighbours.setdefault(token, (set(), set()))
        if index > 0:
            prevs.add(tokens[index - 1])
        if index + 1 < len(tokens):
            nexts.add(tokens[index + 1])
    return neighbours


def _shares_source_neighbour(
    candidate: str,
    original: str,
    page_neighbours: dict[str, tuple[set[str], set[str]]],
    source_neighbours: dict[str, tuple[set[str], set[str]]],
) -> bool:
    """Whether some page occurrence of `candidate` sits next to a word that some source occurrence of `original` also sits next to, on the same side.

    Generators duplicate sentences, so `original` surviving intact elsewhere
    on the page doesn't clear `candidate` -- it could be a damaged copy of a
    *different* occurrence of the same sentence. Requiring a shared neighbour
    at the matching side anchors `candidate` to the specific source spot it
    would have to have come from.
    """
    candidate_prevs, candidate_nexts = page_neighbours.get(candidate, (set(), set()))
    original_prevs, original_nexts = source_neighbours.get(original, (set(), set()))
    return bool(candidate_prevs & original_prevs) or bool(candidate_nexts & original_nexts)


def _is_rewritten_predicate(candidate: str, original: str, source_tokens: list[str]) -> bool:
    """Whether `candidate` re-conjugates `original`, which the source itself uses as a predicate.

    `극대화해 드립니다` is a verb followed by its auxiliary, so `극대화할` is the
    same verb in another ending. A name standing alone, or in front of a title,
    has no such follower and keeps being reported.
    """
    prefix = 0
    for left, right in zip(candidate, original):
        if left != right:
            break
        prefix += 1
    if prefix < _MINIMUM_PRESERVED_STEM:
        return False
    tails = (candidate[prefix:], original[prefix:])
    if not all(tail in _KOREAN_DERIVATIONAL_TAILS for tail in tails):
        return False
    return any(
        token == original
        and (following in _PREDICATE_FOLLOWER_WORDS or following.startswith(_PREDICATE_FOLLOWER_PREFIXES))
        for token, following in zip(source_tokens, source_tokens[1:])
    )


def find_corrupted_tokens(html: str, features: FeatureModel, content_freedom: ContentFreedom = "low") -> list[str]:
    """Report output tokens that are near-misses of a Feature Model identifier.

    Content freedom governs whether a source token may be dropped or replaced,
    never whether it may be restated incorrectly. A token that is absent is
    therefore fine; a token that appears in a slightly different form is not.
    """
    findings: list[str] = _find_institution_substitutions(html, features)
    findings.extend(_find_leaked_placeholder_tokens(html))
    findings.extend(_find_mangled_number_tokens(html, features))
    findings.extend(_find_dropped_numerals(html, features))
    findings.extend(_find_script_mixing(html, features))
    findings.extend(_find_translated_proper_nouns(html, features))
    findings.extend(_find_particle_swaps(html, features))
    findings.extend(_find_mid_token_substitutions(html, features))
    findings.extend(_find_email_substitutions(html, features))

    source = _source_tokens(features)
    if source:
        parser = _TextParser()
        parser.feed(html)
        page_all_tokens = _tokenize(" ".join(parser.text_parts))
        page_tokens = [token for token in page_all_tokens if _is_identifier_like(token)]
        candidates = set(page_tokens)
        page_token_set = set(page_tokens)
        source_text = all_source_text(features)
        source_all_tokens = _tokenize(source_text)
        page_neighbours = _neighbour_map(page_all_tokens)
        source_neighbours = _neighbour_map(source_all_tokens)

        for candidate in sorted(candidates - source):
            for original in source:
                if _is_korean_inflection(candidate, original):
                    continue
                if content_freedom in _PREDICATE_REWRITE_LEVELS and _is_rewritten_predicate(candidate, original, source_all_tokens):
                    continue
                if original in candidate:
                    # `드리겠습니다` inside `답변드리겠습니다`: every character of the
                    # source survives, in order and unbroken. That is a longer word
                    # built around the original, not a damaged copy of it. The
                    # reverse is not safe -- a candidate inside a longer source has
                    # lost characters, which is exactly what corruption looks like.
                    continue
                if _is_rewritten_verb_ending(candidate, original) or _drops_trailing_generic_unit(candidate, original):
                    continue
                if _is_numeric_reformat(candidate, original):
                    continue
                if original in page_token_set and not _shares_source_neighbour(
                    candidate, original, page_neighbours, source_neighbours
                ):
                    # `original` still appears intact elsewhere on the page, and
                    # `candidate` sits among different neighbours than any of
                    # those intact occurrences -- a separate word that merely
                    # resembles it, not a damaged copy of it.
                    continue
                if _differs_by_one_character(candidate, original) or SequenceMatcher(None, candidate, original).ratio() >= _SIMILARITY_THRESHOLD:
                    if _is_digit_restatement(candidate, original, source_text):
                        continue
                    findings.append(f"corrupted source token: '{original}' rendered as '{candidate}'")
                    break

    if findings:
        logger.warning("Corrupted source tokens detected: %s", findings)
    return findings


def _dropped_unit_original(candidate: str, source: set[str]) -> str | None:
    """The single source token if `candidate` restates it with one Hangul unit character dropped.

    `2023부터` for source `2023년부터` is a digit run plus a non-empty remainder
    that matches exactly one source token differing only by a single Hangul
    character inserted right after the digits -- the dropped unit. A bare
    number with no remainder never matches, and an ambiguous source is left
    for the model to judge rather than guessed at.
    """
    match = _MULTIPLIER_TOKEN_RE.match(candidate)
    if not match:
        return None
    digits, rest = match.group(1), match.group(2)
    pattern = re.compile(r"^" + re.escape(digits) + r"[가-힣]" + re.escape(rest) + r"$")
    matches = [original for original in source if pattern.match(original)]
    return matches[0] if len(matches) == 1 else None


def repair_corrupted_tokens(html: str, features: FeatureModel) -> tuple[str, list[str]]:
    """Restore a source token corrupted by a single substituted Hangul character.

    Repaired only when exactly one source token is a one-character-away match with a Hangul
    substitution on both sides -- an ambiguous source is left for the model to judge rather than
    guessed at, as is a substitution that is really a grammatical inflection or that happens to
    spell out an unrelated word already present in the source. Matching is scoped to each text
    node's own decoded data, the same scope `find_corrupted_tokens` reports against, so
    attributes, scripts, styles, templates, and noscript content are left untouched.
    """
    source = _source_tokens(features)
    if not source:
        return html, []
    source_text = all_source_text(features)

    parser = _PositionedTextParser(html)
    parser.feed(html)
    parser.close()

    applied: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for node in parser.nodes:
        decoded_text, meta = _decode_node(node)
        for candidate, start, end in _positioned_tokens(decoded_text):
            if candidate in source or not _is_identifier_like(candidate):
                continue
            matches = [original for original in source if _differs_by_one_hangul_character(candidate, original)]
            if len(matches) == 1:
                original = matches[0]
                if _is_korean_inflection(candidate, original) or candidate in source_text:
                    continue
            else:
                original = _dropped_unit_original(candidate, source)
                if original is None or candidate in source_text:
                    continue
            raw_start = _raw_offset_for_span(meta, start, end)
            if raw_start is None:
                continue
            rewrite = f"'{candidate}' -> '{original}'"
            if rewrite not in applied:
                applied.append(rewrite)
            replacements.append((raw_start, raw_start + (end - start), original))

    if not replacements:
        return html, []

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, text in replacements:
        if start < cursor:
            continue
        pieces.append(html[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces), applied


def repair_particle_swaps(html: str, features: FeatureModel) -> tuple[str, list[str]]:
    """Restore the particle allomorph `_find_particle_swaps` reports as swapped.

    Matching is scoped to each text node's own decoded data, the same scope
    `_find_particle_swaps` reports against, so attributes, scripts, styles,
    templates, and noscript content are left untouched.
    """
    source = _raw_source_tokens(features)
    if not source:
        return html, []

    parser = _PositionedTextParser(html)
    parser.feed(html)
    parser.close()

    applied: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for node in parser.nodes:
        decoded_text, meta = _decode_node(node)
        for token, start, end in _positioned_tokens(decoded_text):
            if token in source:
                continue
            for pair in _PARTICLE_PAIRS:
                matched = False
                for particle in pair:
                    if not token.endswith(particle) or len(token) <= len(particle):
                        continue
                    stem = token[: -len(particle)]
                    has_batchim = _has_batchim(stem[-1], pair)
                    if has_batchim is None:
                        continue
                    correct = _correct_particle(pair, has_batchim)
                    expected = stem + correct
                    if particle == correct or expected not in source:
                        continue
                    raw_start = _raw_offset_for_span(meta, start, end)
                    if raw_start is None:
                        continue
                    rewrite = f"'{token}' -> '{expected}'"
                    if rewrite not in applied:
                        applied.append(rewrite)
                    replacements.append((raw_start, raw_start + (end - start), expected))
                    matched = True
                    break
                if matched:
                    break

    if not replacements:
        return html, []

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, text in replacements:
        if start < cursor:
            continue
        pieces.append(html[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces), applied


def repair_mid_token_substitutions(html: str, features: FeatureModel) -> tuple[str, list[str]]:
    """Restore the middle token `_find_mid_token_substitutions` reports as swapped.

    Matching is scoped to each text node's own decoded data, the same scope
    `_find_mid_token_substitutions` reports against, so attributes, scripts,
    styles, templates, and noscript content are left untouched.
    """
    context, prev_values, source_vocab = _mid_token_context(features)
    if not context:
        return html, []

    applied: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for prev, mid, nxt, meta, mid_start, mid_end in _mid_token_triples(html):
        expected = _mid_token_substitution(prev, mid, nxt, context, prev_values, source_vocab)
        if expected is None:
            continue
        raw_start = _raw_offset_for_span(meta, mid_start, mid_end)
        if raw_start is None:
            continue
        rewrite = f"'{mid}' -> '{expected}'"
        if rewrite not in applied:
            applied.append(rewrite)
        replacements.append((raw_start, raw_start + (mid_end - mid_start), expected))

    if not replacements:
        return html, []

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, text in replacements:
        if start < cursor:
            continue
        pieces.append(html[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces), applied


def repair_email_substitutions(html: str, features: FeatureModel) -> tuple[str, list[str]]:
    """Restore the source email `_find_email_substitutions` reports as swapped.

    Matched across the whole document, the same scope `_find_email_substitutions`
    reports against, so a corrupted `mailto:` href is repaired even where the
    link text still shows the untouched address -- except `<script>`/`<style>`
    content, which is left untouched.
    """
    source_emails = set(_EMAIL_RE.findall(all_source_text(features)))
    if not source_emails:
        return html, []

    applied: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for match in _visible_email_matches(html):
        email = match.group(0)
        if email in source_emails:
            continue
        domain = email.split("@", 1)[1]
        matches = [source_email for source_email in source_emails if source_email.split("@", 1)[1] == domain]
        if len(matches) != 1:
            continue
        original = matches[0]
        rewrite = f"'{email}' -> '{original}'"
        if rewrite not in applied:
            applied.append(rewrite)
        replacements.append((match.start(), match.end(), original))

    if not replacements:
        return html, []

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, text in replacements:
        if start < cursor:
            continue
        pieces.append(html[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(html[cursor:])
    return "".join(pieces), applied
