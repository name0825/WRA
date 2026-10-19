import logging
import re
from dataclasses import dataclass
from typing import Any

from wra.core.models import FeatureModel

logger = logging.getLogger(__name__)

_EXCLUDED_KEYS = ("source_assets", "media")

_EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_URL_PATTERN = r"https?://[^\s\"'<>]+"
_PHONE_PATTERN = (
    r"(?<!\d)(?<!\d-)(?<!\d )"
    r"(?:"
    r"\+\d{1,3}\s?\(\d{2,4}\)\s?\d{3,4}[\s-]\d{4}"
    r"|\+\d{1,3}\s?\d{1,4}[\s-]\d{3,4}[\s-]\d{4}"
    r"|\(\d{1,4}\)\s?\d{3,4}[\s-]\d{4}"
    r"|0\d{1,2}[\s-]\d{3,4}[\s-]\d{4}"
    r"|\d{3}[\s-]\d{3,4}[\s-]\d{4}"
    r")"
    r"(?!\d)(?![\s-]\d)"
)

_LITERAL_RE = re.compile(f"(?P<email>{_EMAIL_PATTERN})|(?P<url>{_URL_PATTERN})|(?P<phone>{_PHONE_PATTERN})")
_TOKEN_RE = re.compile(r"WRA(?:<[^>]*>|[\s_.\-])*REF(?:<[^>]*>|[\s_.\-])*(\d+)", re.IGNORECASE)
_MANGLED_TOKEN_RE = re.compile(r"\bWRA(?:<[^>]*>|[\s_.\-])*([A-Z]{1,4})(?:<[^>]*>|[\s_.\-])*(\d+)(?![A-Za-z0-9])")


@dataclass(frozen=True)
class PlaceholderMap:
    values: dict[str, str]

    def __bool__(self) -> bool:
        return bool(self.values)


def _is_valid_phone(candidate: str) -> bool:
    digits = re.sub(r"\D", "", candidate)
    return 9 <= len(digits) <= 15


def _matched_literal(match: re.Match) -> str | None:
    if match.group("phone") is not None:
        candidate = match.group("phone")
        return candidate if _is_valid_phone(candidate) else None
    return match.group(0)


def protect_features(features: FeatureModel) -> tuple[FeatureModel, PlaceholderMap]:
    literal_to_token: dict[str, str] = {}
    token_to_literal: dict[str, str] = {}

    def token_for(literal: str) -> str:
        token = literal_to_token.get(literal)
        if token is None:
            token = f"WRAREF{len(literal_to_token) + 1}"
            literal_to_token[literal] = token
            token_to_literal[token] = literal
        return token

    def protect_string(text: str) -> str:
        def substitute(match: re.Match) -> str:
            literal = _matched_literal(match)
            if literal is None:
                return match.group(0)
            return token_for(literal)

        return _LITERAL_RE.sub(substitute, text)

    def protect_value(value: Any) -> Any:
        if isinstance(value, str):
            return protect_string(value)
        if isinstance(value, dict):
            return {key: protect_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [protect_value(item) for item in value]
        return value

    data = features.model_dump()
    protected = {key: (value if key in _EXCLUDED_KEYS else protect_value(value)) for key, value in data.items()}
    return FeatureModel.model_validate(protected), PlaceholderMap(values=token_to_literal)


def protect_text(text: str, mapping: PlaceholderMap) -> str:
    literal_to_token = {literal: token for token, literal in mapping.values.items()}

    def substitute(match: re.Match) -> str:
        literal = _matched_literal(match)
        if literal is None:
            return match.group(0)
        return literal_to_token.get(literal, match.group(0))

    return _LITERAL_RE.sub(substitute, text)


def restore(text: str, mapping: PlaceholderMap) -> tuple[str, int]:
    unresolved = 0

    def substitute(match: re.Match) -> str:
        nonlocal unresolved
        literal = mapping.values.get(f"WRAREF{match.group(1)}")
        if literal is None:
            unresolved += 1
            return ""
        return literal

    restored = _TOKEN_RE.sub(substitute, text)

    mangled_restored = 0

    def substitute_mangled(match: re.Match) -> str:
        nonlocal mangled_restored
        letters = match.group(1)
        digits = match.group(2)
        if letters == "REF":
            return match.group(0)
        # Ordinary words like "WRAP1" or "WRAABC123" are too common to treat as
        # damage unless the letters plausibly are what a corrupted "REF" left
        # behind, i.e. they still contain at least one of its letters.
        if not any(letter in ("R", "E", "F") for letter in letters):
            return match.group(0)
        literal = mapping.values.get(f"WRAREF{digits}")
        if literal is None:
            return match.group(0)
        mangled_restored += 1
        return literal

    restored = _MANGLED_TOKEN_RE.sub(substitute_mangled, restored)

    if unresolved:
        logger.warning("Unresolved placeholder tokens stripped from output: %d", unresolved)
    if mangled_restored:
        logger.warning("Mangled placeholder tokens restored from output: %d", mangled_restored)
    return restored, unresolved
