"""Prompt-aware context sizing.

Ollama silently drops the *front* of a prompt that exceeds ``num_ctx``, which is
exactly where role, schema, and output-contract instructions live. Sizing the
initial context window from the actual prompt keeps those instructions inside
the window instead of relying on recovery retries to discover the truncation.
"""

# Conservative characters-per-token ratio. Mixed English/CJK prompts tokenize
# closer to 1.5-3 chars per token, so 2.5 avoids under-sizing the window.
_CHARS_PER_TOKEN = 2.5

# Headroom for the model's own output plus tokenizer variance.
_OUTPUT_HEADROOM_TOKENS = 2048

# Ceiling applied to the *initial* estimate. Recovery may escalate beyond this.
_INITIAL_CEILING = 32768


def estimate_num_ctx(prompt: str, minimum: int, maximum: int, ceiling: int | None = _INITIAL_CEILING) -> int:
    """Return a power-of-two context window large enough to hold ``prompt``."""
    required = int(len(prompt) / _CHARS_PER_TOKEN) + _OUTPUT_HEADROOM_TOKENS
    size = max(minimum, 1 << max(0, required - 1).bit_length())
    upper = maximum if ceiling is None else min(maximum, ceiling)
    return max(minimum, min(size, upper)) if minimum <= upper else minimum
