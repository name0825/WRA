from dataclasses import dataclass
from enum import Enum


class HTMLRecoveryError(str, Enum):
    TOKEN_LIMIT = "token_limit"
    STRUCTURE = "structure"
    CONTENT = "content"
    UNKNOWN = "unknown"


class RecoveryStrategy(str, Enum):
    INCREASE_CONTEXT = "increase_context"
    REVISE_RESPONSE = "revise_response"
    REGENERATE = "regenerate"


@dataclass(frozen=True)
class RecoveryPolicy:
    max_retries: int = 3
    initial_num_ctx: int = 8192
    max_num_ctx: int = 32768
    context_multiplier: float = 2.0

    def next_num_ctx(self, current: int) -> int:
        if current >= self.max_num_ctx:
            return current
        next_value = max(current + 1, int(current * self.context_multiplier))
        return min(next_value, self.max_num_ctx)


def _looks_truncated_html(response: str | None) -> bool:
    """Detect an HTML response that likely stopped because the output budget was exhausted."""
    if not response:
        return False

    text = response.strip().lower()
    if "<html" not in text or "</html>" in text:
        return False

    score = 2  # An HTML document without </html> is already a strong signal.

    # A response cut inside one of the document's major containers is stronger evidence.
    if "<head" in text and "</head>" not in text:
        score += 2
    if "<body" in text and "</body>" not in text:
        score += 2

    # LLM output commonly ends mid-CSS/JS/attribute when the output budget is exhausted.
    if text.count("{") > text.count("}"):
        score += 2
    if text.count("<script") > text.count("</script>"):
        score += 1
    if text.count("<style") > text.count("</style>"):
        score += 1

    # Very short malformed documents are more likely a model/structure failure than truncation.
    if len(text) >= 1500:
        score += 1

    return score >= 4


def classify_response(response: str | None) -> HTMLRecoveryError:
    """Classify a successful HTTP/LLM response that may have been truncated."""
    if _looks_truncated_html(response):
        return HTMLRecoveryError.TOKEN_LIMIT
    return HTMLRecoveryError.UNKNOWN


def classify_exception(exc: Exception, response: str | None = None) -> HTMLRecoveryError:
    message = str(exc).lower()
    token_markers = (
        "context length",
        "context window",
        "maximum context",
        "num_ctx",
        "token limit",
        "too many tokens",
        "prompt is too long",
        "maximum number of tokens",
        "max tokens",
        "done_reason=length",
        "done reason: length",
        "output limit",
    )
    if any(marker in message for marker in token_markers):
        return HTMLRecoveryError.TOKEN_LIMIT

    # Ollama can return HTTP 200 even when generation stops because the output
    # budget is exhausted. In that case there is no exception to classify, so
    # use the actual response shape as a secondary signal.
    response_error = classify_response(response)
    if response_error is HTMLRecoveryError.TOKEN_LIMIT:
        return response_error

    if "complete html document" in message or "doctype" in message or "</head>" in message:
        return HTMLRecoveryError.STRUCTURE

    return HTMLRecoveryError.UNKNOWN


def classify_validation_errors(errors: list[str]) -> HTMLRecoveryError:
    if any("missing required content" in error.lower() for error in errors):
        return HTMLRecoveryError.CONTENT
    if any(
        marker in error.lower()
        for error in errors
        for marker in (
            "missing required link",
            "missing required image",
            "missing required video",
            "missing required cta",
            "malformed required link",
            "missing doctype",
            "html",
            "dom",
            "structure",
            "duplicate",
        )
    ):
        return HTMLRecoveryError.STRUCTURE
    return HTMLRecoveryError.UNKNOWN


def strategy_for(error: HTMLRecoveryError) -> RecoveryStrategy:
    if error is HTMLRecoveryError.TOKEN_LIMIT:
        return RecoveryStrategy.INCREASE_CONTEXT
    if error in {HTMLRecoveryError.STRUCTURE, HTMLRecoveryError.CONTENT}:
        return RecoveryStrategy.REVISE_RESPONSE
    return RecoveryStrategy.REGENERATE


def build_recovery_prompt(
    original_prompt: str,
    previous_response: str | None,
    error: HTMLRecoveryError,
    details: list[str],
    revision_template: str,
) -> str:
    if strategy_for(error) is RecoveryStrategy.REGENERATE:
        return original_prompt

    validation_errors = "\n".join(f"- {detail}" for detail in details) or "- unspecified validation failure"
    revision = revision_template.format(
        failure_type=error.value,
        validation_errors=validation_errors,
        previous_response=previous_response or "(no previous response)",
    )
    return f"{original_prompt}\n\n{revision}"
