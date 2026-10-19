"""Retry shaping shared by every stage that recovers from a failed LLM call.

The designer and the generator recover from different failures but retry on the
same terms: a bounded number of attempts on a context window that grows toward a
ceiling. Keeping one policy here stops the two curves from drifting apart.
"""

from dataclasses import dataclass
from enum import Enum

from wra.llm.context import growth_num_ctx


class RecoveryStrategy(str, Enum):
    INCREASE_CONTEXT = "increase_context"
    REVISE_RESPONSE = "revise_response"
    REGENERATE = "regenerate"


@dataclass(frozen=True)
class RecoveryPolicy:
    max_retries: int = 3
    initial_num_ctx: int = 8192
    max_num_ctx: int = 32768
    ceiling_num_ctx: int = 32768
    growth_power: float = 2.0
    output_headroom_tokens: int = 2048

    def num_ctx_for_attempt(self, attempt: int, base: int) -> int:
        """Return the window for ``attempt``, growing from ``base`` to ``max_num_ctx``.

        ``base`` is the first attempt's estimated window, so the curve never
        starts below what the prompt already needs.
        """
        total_attempts = self.max_retries + 1
        if attempt > total_attempts:
            return min(base, self.max_num_ctx)
        return growth_num_ctx(attempt, total_attempts, base, self.max_num_ctx, self.growth_power)


# Every phrasing either Ollama or a wrapped model has used to say the request or
# the response ran out of tokens. One list because a marker seen by one stage is
# just as valid for the other.
_TOKEN_LIMIT_MARKERS = (
    "context length",
    "context window",
    "maximum context",
    "num_ctx",
    "token limit",
    "too many tokens",
    "prompt is too long",
    "output token",
    "max tokens",
    "maximum tokens",
    "maximum number of tokens",
    "length limit",
    "done_reason=length",
    "done reason: length",
    "output limit",
)


def is_token_limit_message(message: str) -> bool:
    """Whether an error message reports an exhausted context or output budget."""
    lowered = message.lower()
    return any(marker in lowered for marker in _TOKEN_LIMIT_MARKERS)
