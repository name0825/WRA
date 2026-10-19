import re
import unicodedata

_MARKER = re.compile(r"<0x([0-9A-Fa-f]{2})>")
_RUN = re.compile(r"(?:<0x[0-9A-Fa-f]{2}>)+")


def _decode_run(match: re.Match[str]) -> str:
    run = match.group(0)
    raw = bytes(int(hex_byte, 16) for hex_byte in _MARKER.findall(run))
    decoded = raw.decode("utf-8", errors="ignore")
    decoded = decoded.replace(" ", " ")
    return "".join(ch for ch in decoded if unicodedata.category(ch) != "Cf")


def strip_byte_fallback_markers(text: str) -> tuple[str, int]:
    """Strip SentencePiece byte-fallback markers (e.g. ``<0xC2>``) leaked as
    literal text by local Ollama models. Returns the sanitized text and the
    number of markers found.
    """
    marker_count = len(_MARKER.findall(text))
    if marker_count == 0:
        return text, 0
    return _RUN.sub(_decode_run, text), marker_count
