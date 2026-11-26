import re

from wra.validator.tokens import extract_visible_text

_CODE_RE = re.compile(r"<(code|pre)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_URL_OR_EMAIL_RE = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MIN_LETTERS = 200
_KO_INFERENCE_SHARE = 0.3
_KO_DRIFT_SHARE = 0.15
_EN_DRIFT_SHARE = 0.5
_EN_INFERENCE_LATIN_SHARE = 0.8
_EN_INFERENCE_HANGUL_SHARE = 0.02


def _is_hangul(char: str) -> bool:
    return "가" <= char <= "힣" or "ㄱ" <= char <= "ㆎ"


def _is_latin(char: str) -> bool:
    return ("a" <= char <= "z") or ("A" <= char <= "Z")


def _script_counts(text: str) -> tuple[int, int, int]:
    hangul = latin = letters = 0
    for char in text:
        if not char.isalpha():
            continue
        letters += 1
        if _is_hangul(char):
            hangul += 1
        elif _is_latin(char):
            latin += 1
    return hangul, latin, letters


def infer_language(text: str) -> str | None:
    """`ko` when Hangul is a real share of the letters, `en` when Latin dominates with no Hangul, else None."""
    hangul, latin, letters = _script_counts(text)
    if not letters:
        return None
    if hangul / letters >= _KO_INFERENCE_SHARE:
        return "ko"
    if latin / letters >= _EN_INFERENCE_LATIN_SHARE and hangul / letters < _EN_INFERENCE_HANGUL_SHARE:
        return "en"
    return None


def output_language_rule(language: str) -> str:
    return (
        f"Output language is fixed to `{language}`: every heading, paragraph, list item, button label, alt text and aria-label is written in it; "
        f"never translate the brief; names, URLs and technical terms stay as written; `<html lang=\"{language}\">`."
    )


def find_language_drift(html: str, language: str | None) -> list[str]:
    """Flag a page whose body text is written in a different script than the expected language."""
    if not language:
        return []
    expected = language.strip().lower()[:2]
    if expected not in ("ko", "en"):
        return []
    text = _URL_OR_EMAIL_RE.sub(" ", extract_visible_text(_CODE_RE.sub(" ", html)))
    hangul, _, letters = _script_counts(text)
    if letters < _MIN_LETTERS:
        return []
    share = hangul / letters
    if expected == "ko" and share < _KO_DRIFT_SHARE:
        return ["output language drifted: expected ko, page is mostly Latin"]
    if expected == "en" and share > _EN_DRIFT_SHARE:
        return ["output language drifted: expected en, page is mostly Hangul"]
    return []
