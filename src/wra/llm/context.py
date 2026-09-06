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


# Separators for JSON that goes into a prompt. Indentation is worth 40% of the
# block's tokens and buys the model nothing -- measured on a real FeatureModel:
# 3095 tokens indented against 1799 compact. JSON written to disk stays indented.
COMPACT_JSON = (",", ":")

def estimate_num_ctx(prompt: str, minimum: int, maximum: int, ceiling: int | None = _INITIAL_CEILING, headroom_tokens: int = _OUTPUT_HEADROOM_TOKENS) -> int:
    """Return a power-of-two context window large enough to hold ``prompt``."""
    required = int(len(prompt) / _CHARS_PER_TOKEN) + headroom_tokens
    size = max(minimum, 1 << max(0, required - 1).bit_length())
    upper = maximum if ceiling is None else min(maximum, ceiling)
    return max(minimum, min(size, upper)) if minimum <= upper else minimum


def growth_num_ctx(attempt: int, total_attempts: int, initial: int, maximum: int, growth_power: float) -> int:
    """Return the context window for ``attempt`` on an accelerating growth curve.

    ``attempt`` is 1-based and counts total attempts, not retries, so the final
    attempt lands exactly on ``maximum``. ``growth_power`` shapes the curve:
    above 1.0 keeps the early retries small and saves the large window for the
    last attempt, which matters when an oversized window spills the KV cache
    out of VRAM.
    """
    if attempt < 1 or attempt > total_attempts:
        raise ValueError(f"attempt must be between 1 and {total_attempts}, got {attempt}")
    if total_attempts == 1 or attempt == 1 or initial >= maximum:
        return min(initial, maximum)
    progress = (attempt - 1) / (total_attempts - 1)
    scaled = initial + (progress ** growth_power) * (maximum - initial)
    return min(int(scaled), maximum)
