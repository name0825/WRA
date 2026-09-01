import json
import re
from typing import Any


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.IGNORECASE | re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def _json_object_candidates(response: str) -> list[str]:
    """Return complete JSON object candidates embedded in arbitrary text."""
    candidates: list[str] = []
    decoder = json.JSONDecoder()
    start = 0

    while True:
        index = response.find("{", start)
        if index < 0:
            break
        try:
            _, end = decoder.raw_decode(response[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue
        candidates.append(response[index:index + end].strip())
        start = index + 1

    return candidates


def normalize_json_response(response: str) -> str:
    """Normalize common LLM wrappers around a JSON object.

    LLMs can return Markdown fences, reasoning, prose, or even a malformed
    first JSON attempt followed by a corrected JSON attempt. Prefer a complete
    JSON object from a fenced block; otherwise find complete JSON objects in
    the surrounding text. Malformed/truncated JSON is left untouched so the
    caller can report a real parsing failure.
    """
    normalized = response.lstrip("\ufeff").strip()
    normalized = _THINK_BLOCK_RE.sub("", normalized).strip()
    normalized = _THINK_OPEN_RE.sub("", normalized).strip()

    fenced_candidates: list[str] = []
    for match in _JSON_FENCE_RE.finditer(normalized):
        candidate = match.group(1).strip()
        try:
            data: Any = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            fenced_candidates.append(candidate)

    if fenced_candidates:
        return max(fenced_candidates, key=len)

    candidates = _json_object_candidates(normalized)
    valid_candidates: list[str] = []
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            valid_candidates.append(candidate)

    if valid_candidates:
        return max(valid_candidates, key=len)

    return normalized
