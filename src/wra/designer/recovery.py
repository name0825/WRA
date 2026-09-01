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
        DesignRecoveryError.INVALID_DESIGN,
    }:
        return RecoveryStrategy.REVISE_RESPONSE
    return RecoveryStrategy.REGENERATE


def _schema_contract() -> str:
    return (
        "DesignSpec required structure: top-level keys are `design`, `sections`, `responsive`, `interaction`. "
        "`design` contains `concept`, `visual_direction`, `layout`, `color_system`, `typography`, `framework`, `motion`. "
        "`color_system` contains `background`, `surface`, `text`, `muted_text`, `primary`, `accent`. "
        "`typography` contains `heading`, `body`, `scale`, `weight`; `weight` must be a single string, not an object. "
        "`framework` contains `css` and `js`, each with `name` and `reason`. "
        "`motion` contains `level` (`none`, `low`, `mid`, or `high`) and `principles`. "
        "Each section contains `id`, `purpose`, `layout`, and `components`; each component contains `type`, `purpose`, `content_source`. "
        "`responsive` contains `strategy`, `breakpoints`, and `behavior`; `behavior` must be an array of strings, not objects. "
        "`interaction` contains `principles` and `components`; each interaction component contains `target` and `behavior`."
    )


def _reference_contract() -> str:
    return (
        "Critical reference rules: `content_source` must be a Feature Model dotted path, never literal content. "
        "Valid field roots include `metadata.title`, `metadata.description`, `content.headings`, `content.paragraphs`, "
        "`content.lists`, `content.tables`, `content.buttons`, `content.links`, `content.labels`, `content.text`, "
        "`structure.sections`, `structure.forms`, `media.images`, `media.videos`, `media.icons`, `media.urls`, "
        "`business.contact`, `business.email`, `business.phone`, `navigation.items`, `requirements.must_preserve`, "
        "`site.type`, and `site.industry`. Never use forms such as `text: '...'`, `metadata.title: '...'`, or "
        "`content.text: '...'` as content_source. `interaction.components[].target` must exactly equal one of the "
        "component `type` values declared in `sections[].components`; do not invent pluralized or descriptive targets."
    )


def build_recovery_prompt(
    original_prompt: str,
    previous_response: str | None,
    error: DesignRecoveryError,
    details: list[str],
    recovery_context: str | None = None,
) -> str:
    strategy = strategy_for(error)
    if strategy is RecoveryStrategy.REGENERATE:
        return original_prompt

    validation_errors = "\n".join(f"- {detail}" for detail in details) or "- unspecified validation failure"

    if error is DesignRecoveryError.TOKEN_LIMIT:
        return (
            f"{original_prompt}\n\n"
            "# WRA Output Recovery\n"
            "The previous Designer attempt appears to have exhausted its output token budget. "
            "Regenerate the DesignSpec from the input above using the larger context window. "
            "Return exactly one complete JSON object. Do not output reasoning, Markdown, code fences, or prose. "
            "Keep descriptions concise enough to finish every required field and close every JSON object/array."
        )

    previous = previous_response or "(no previous response)"
    context = recovery_context or "(Feature Model context unavailable)"

    if strategy is RecoveryStrategy.REVISE_RESPONSE:
        return (
            "# WRA DesignSpec Recovery\n\n"
            "Repair the previous Designer response. This is a repair task, not a new design task.\n"
            "Preserve the useful design/content decisions already present in the previous response.\n"
            "Use the Feature Model and available reference paths below when repairing content_source values.\n"
            "Use only configured framework names listed below; framework names are case-sensitive exact matches.\n"
            "Return exactly one valid JSON object and nothing else. Do not output analysis, reasoning, Markdown, code fences, or HTML.\n\n"
            f"{_schema_contract()}\n\n"
            f"{_reference_contract()}\n\n"
            f"{context}\n\n"
            f"Failure type: {error.value}\n\n"
            f"Validation errors:\n{validation_errors}\n\n"
            f"Previous response:\n{previous}\n"
        )

    return original_prompt
