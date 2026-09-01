import re


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.IGNORECASE | re.DOTALL)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


def normalize_json_response(response: str) -> str:
    """Normalize common LLM wrappers around a JSON object.

    LLMs sometimes return valid JSON inside Markdown fences, prepend reasoning,
    or add a short sentence before/after the JSON despite structured-output
    instructions. Keep the actual JSON payload while leaving malformed or
    truncated JSON untouched so callers can classify the parsing failure.
    """
    normalized = response.lstrip("\ufeff").strip()
    normalized = _THINK_BLOCK_RE.sub("", normalized).strip()
    normalized = _THINK_OPEN_RE.sub("", normalized).strip()

    match = _JSON_FENCE_RE.match(normalized)
    if match:
        return match.group(1).strip()

    start = normalized.find("{")
    end = normalized.rfind("}")
    if start > 0 and end > start:
        return normalized[start:end + 1].strip()

    return normalized
