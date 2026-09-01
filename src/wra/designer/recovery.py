from dataclasses import dataclass
from enum import Enum


class DesignRecoveryError(str, Enum):
    TOKEN_LIMIT = "token_limit"
    JSON_STRUCTURE = "json_structure"
    MISSING_CONTENT = "missing_content"
    INVALID_CONTENT = "invalid_content"
    INVALID_DESIGN = "invalid_design"
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


def classify_exception(exc: Exception) -> DesignRecoveryError:
    message = str(exc).lower()
    token_markers = (
        "context length",
        "context window",
        "maximum context",
        "num_ctx",
        "token limit",
        "too many tokens",
        "prompt is too long",
    )
    if any(marker in message for marker in token_markers):
        return DesignRecoveryError.TOKEN_LIMIT
    return DesignRecoveryError.UNKNOWN


def strategy_for(error: DesignRecoveryError) -> RecoveryStrategy:
    if error is DesignRecoveryError.TOKEN_LIMIT:
        return RecoveryStrategy.INCREASE_CONTEXT
    if error in {
        DesignRecoveryError.JSON_STRUCTURE,
        DesignRecoveryError.MISSING_CONTENT,
        DesignRecoveryError.INVALID_CONTENT,
    }:
        return RecoveryStrategy.REVISE_RESPONSE
    return RecoveryStrategy.REGENERATE


def build_recovery_prompt(
    original_prompt: str,
    previous_response: str | None,
    error: DesignRecoveryError,
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
