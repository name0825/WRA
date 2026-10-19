import html
import json
import logging
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from wra.core.models import FeatureModel
from wra.llm.client import LLMClient
from wra.llm.response import normalize_json_response
from wra.validator.tokens import _HAN_KANA_RE, extract_visible_text, find_script_mixing_tokens

logger = logging.getLogger(__name__)

_SKIP_TAGS = frozenset({"script", "style", "template", "noscript"})
_CONTEXT_WIDTH = 60
_SOURCE_SNIPPET_LIMIT = 5
_WORD_RE = re.compile(r"\S+")
_FOREIGN_RUN_RE = re.compile(_HAN_KANA_RE.pattern + "+")
_CJK_PUNCTUATION_MAP = {
    "。": ".",
    "、": ",",
    "，": ",",
    "！": "!",
    "？": "?",
    "：": ":",
    "；": ";",
}


def _line_starts(text: str) -> list[int]:
    """Absolute offset of the start of each line in `text`, indexed by `lineno - 1`."""
    starts = [0]
    index = text.find("\n")
    while index != -1:
        starts.append(index + 1)
        index = text.find("\n", index + 1)
    return starts


class _TextNodeCollector(HTMLParser):
    """Record the absolute span and raw text of each visible text node in `html`.

    Content inside `script`/`style`/`template`/`noscript` is skipped. Positions
    come from `getpos()`, converted to an absolute index into `html`, so the
    caller can splice replacements into the original string without touching
    tags, attributes, comments, declarations, or entities.
    """

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_starts = _line_starts(html)
        self._skip_depth = 0
        self.spans: list[tuple[int, str]] = []

    def _offset(self) -> int:
        lineno, col = self.getpos()
        return self._line_starts[lineno - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.spans.append((self._offset(), data))


def _rewrite_token_runs(data: str, replacements: dict[str, str]) -> str:
    """Replace whitespace-delimited runs of `data` that exactly match a flagged token."""

    def substitute(match: re.Match[str]) -> str:
        replacement = replacements.get(match.group(0))
        if replacement is None:
            return match.group(0)
        return html.escape(replacement, quote=False)

    return _WORD_RE.sub(substitute, data)


def _rewrite_text_nodes(source: str, replacements: dict[str, str]) -> str:
    """Splice `replacements` into `source`'s visible text nodes, leaving everything else byte-identical."""
    collector = _TextNodeCollector(source)
    collector.feed(source)
    pieces: list[str] = []
    cursor = 0
    for start, data in collector.spans:
        pieces.append(source[cursor:start])
        pieces.append(_rewrite_token_runs(data, replacements))
        cursor = start + len(data)
    pieces.append(source[cursor:])
    return "".join(pieces)


def _sentence_context(visible_text: str, token: str) -> str:
    index = visible_text.find(token)
    if index < 0:
        return token
    start = max(0, index - _CONTEXT_WIDTH)
    end = min(len(visible_text), index + len(token) + _CONTEXT_WIDTH)
    return " ".join(visible_text[start:end].split())


def _source_snippets(features: FeatureModel, token: str) -> list[str]:
    hangul_chars = {character for character in token if "가" <= character <= "힣"}
    if not hangul_chars:
        return []
    snippets: list[str] = []

    def visit(value) -> None:
        if isinstance(value, str):
            if any(character in hangul_chars for character in value):
                snippets.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(features.model_dump(exclude_none=True))
    return snippets[:_SOURCE_SNIPPET_LIMIT]


def _normalize_for_matching(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def _normalize_replacement_punctuation(replacement: str, token: str) -> str:
    """Map CJK/fullwidth punctuation in `replacement` to ASCII, unless it also occurs in `token`."""
    return "".join(
        _CJK_PUNCTUATION_MAP[character] if character in _CJK_PUNCTUATION_MAP and character not in token else character
        for character in replacement
    )


def _strip_common_affixes(key: str, value: str) -> tuple[str, str, str, str]:
    prefix = 0
    while prefix < len(key) and prefix < len(value) and key[prefix] == value[prefix]:
        prefix += 1
    suffix = 0
    while suffix < len(key) - prefix and suffix < len(value) - prefix and key[-1 - suffix] == value[-1 - suffix]:
        suffix += 1
    return (
        key[prefix : len(key) - suffix],
        value[prefix : len(value) - suffix],
        key[:prefix] + key[len(key) - suffix :],
        value[:prefix] + value[len(value) - suffix :],
    )


def _learn_foreign_run_mappings(data: dict) -> dict[str, str | None]:
    """Foreign-script run -> Korean replacement, inferred from key/value pairs whose only
    difference (after stripping the shared prefix and suffix) is that run, wholly contained
    in `key_mid` with no foreign-run characters anywhere else in the pair. A run mapped to
    more than one distinct value is recorded as `None` so it is treated as ambiguous."""
    learned: dict[str, str | None] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if _HAN_KANA_RE.search(value):
            continue
        key_mid, value_mid, key_affixes, _value_affixes = _strip_common_affixes(key, value)
        if not key_mid:
            continue
        if not all(_HAN_KANA_RE.match(character) for character in key_mid):
            continue
        if _HAN_KANA_RE.search(key_affixes):
            continue
        if key_mid in learned and learned[key_mid] != value_mid:
            learned[key_mid] = None
        else:
            learned[key_mid] = value_mid
    return learned


def _resolve_single_run_fallback(token: str, data: dict, features: FeatureModel) -> str | None:
    """Fallback for a token that is Korean text `K` plus exactly one foreign run `R` at its
    start or end, used when the LLM rewrote the key instead of returning a usable value. Builds
    replacement candidates from each response value's non-shared tail with `K` and accepts one
    only if it occurs verbatim in the source text and is itself free of script mixing."""
    runs = list(_FOREIGN_RUN_RE.finditer(token))
    if len(runs) != 1:
        return None
    run = runs[0]
    if run.start() == 0:
        position = "start"
        korean = token[run.end() :]
    elif run.end() == len(token):
        position = "end"
        korean = token[: run.start()]
    else:
        return None
    if len(korean) < 2:
        return None

    accepted: set[str] = set()
    for value in data.values():
        if not isinstance(value, str):
            continue
        if position == "end":
            prefix = 0
            while prefix < len(korean) and prefix < len(value) and korean[prefix] == value[prefix]:
                prefix += 1
            tail = value[prefix:]
            candidates = [korean + " " + tail, korean + tail]
        else:
            suffix = 0
            while suffix < len(korean) and suffix < len(value) and korean[-1 - suffix] == value[-1 - suffix]:
                suffix += 1
            tail = value[: len(value) - suffix]
            candidates = [tail + " " + korean, tail + korean]
        if len(tail) < 2 or _FOREIGN_RUN_RE.search(tail) or any(character in tail for character in "<>&"):
            continue
        for candidate in candidates:
            if any(candidate in snippet for snippet in _source_snippets(features, candidate)) and not find_script_mixing_tokens(
                candidate, features
            ):
                accepted.add(candidate)

    if len(accepted) == 1:
        return next(iter(accepted))
    return None


def repair_script_mixing(
    html: str,
    features: FeatureModel,
    llm_client: LLMClient,
    model: str,
    prompt_path: str | Path = "prompts/generator/script_repair.md",
    *,
    think: bool | None = None,
    timeout: float | None = None,
) -> tuple[str, list[str]]:
    """Ask the generator model for a Korean-only replacement of each leaked-script token.

    Deterministic repair cannot tell a corrupted token from an intentional one;
    that needs meaning. One short LLM call is made only when leaked tokens are
    actually found. A replacement is applied only where it is non-empty and the
    same script-mixing check finds nothing wrong with it; any LLM or JSON
    failure leaves the page unchanged for detection and scoring to handle.
    """
    tokens = find_script_mixing_tokens(html, features)
    if not tokens:
        return html, []

    visible_text = extract_visible_text(html)
    items = [
        {
            "token": token,
            "sentence": _sentence_context(visible_text, token),
            "source_context": _source_snippets(features, token),
        }
        for token in tokens
    ]
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    prompt = f"{prompt_template}\n\nTokens:\n{json.dumps(items, ensure_ascii=False)}"

    try:
        response = llm_client.generate(prompt=prompt, model=model, think=think, timeout=timeout)
        data = json.loads(normalize_json_response(response))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    except Exception:
        logger.warning("Script-mixing repair call failed, leaving tokens as-is: %s", tokens)
        return html, []

    replacements: dict[str, str] = {}
    unresolved: list[str] = []
    for token in tokens:
        replacement = data.get(token)
        if replacement is None:
            normalized_token = _normalize_for_matching(token)
            candidates = [
                value
                for key, value in data.items()
                if _normalize_for_matching(key) == normalized_token
            ]
            if len(candidates) == 1:
                replacement = candidates[0]
        if not isinstance(replacement, str) or not replacement:
            unresolved.append(token)
            continue
        replacement = _normalize_replacement_punctuation(replacement, token)
        if any(character in replacement for character in "<>&"):
            unresolved.append(token)
            continue
        if find_script_mixing_tokens(replacement, features):
            unresolved.append(token)
            continue
        replacements[token] = replacement

    if unresolved:
        learned_runs = _learn_foreign_run_mappings(data)
        still_unresolved: list[str] = []
        for token in unresolved:
            runs = set(_FOREIGN_RUN_RE.findall(token))
            mapping = {run: learned_runs.get(run) for run in runs}
            if not runs or any(korean is None for korean in mapping.values()):
                still_unresolved.append(token)
                continue
            replacement = _FOREIGN_RUN_RE.sub(lambda match: mapping[match.group(0)], token)
            replacement = _normalize_replacement_punctuation(replacement, token)
            if (
                not replacement
                or any(character in replacement for character in "<>&")
                or find_script_mixing_tokens(replacement, features)
            ):
                still_unresolved.append(token)
                continue
            replacements[token] = replacement
        unresolved = still_unresolved

    if unresolved:
        still_unresolved = []
        for token in unresolved:
            replacement = _resolve_single_run_fallback(token, data, features)
            if replacement is None:
                still_unresolved.append(token)
                continue
            replacements[token] = replacement
        unresolved = still_unresolved

    if unresolved:
        logger.warning("Script-mixing repair produced no usable replacement for: %s", unresolved)

    if not replacements:
        return html, []

    repaired = _rewrite_text_nodes(html, replacements)

    rewrites = [f"'{token}' -> '{replacement}'" for token, replacement in replacements.items()]
    return repaired, rewrites
