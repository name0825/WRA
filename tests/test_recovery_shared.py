"""The token-limit vocabulary shared by the designer and the generator."""

import pytest

from wra.designer.recovery import DesignRecoveryError
from wra.designer.recovery import classify_exception as classify_design
from wra.generator.recovery import HTMLRecoveryError
from wra.generator.recovery import classify_exception as classify_html
from wra.llm.recovery import RecoveryPolicy, is_token_limit_message


@pytest.mark.parametrize("message", [
    "LLM generation stopped due to output token limit (Ollama done_reason=length)",
    "prompt is too long for the context window",
    "num_ctx exceeded",
    "maximum number of tokens reached",
    "output limit reached",
    "length limit reached",
])
def test_both_stages_read_the_same_token_limit_messages(message: str) -> None:
    """The two stages kept separate marker lists; a message known to one is real for the other."""
    assert is_token_limit_message(message)
    assert classify_design(RuntimeError(message)) is DesignRecoveryError.TOKEN_LIMIT
    assert classify_html(RuntimeError(message)) is HTMLRecoveryError.TOKEN_LIMIT


@pytest.mark.parametrize("message", [
    "invalid JSON: unexpected character",
    "does not match DesignSpec",
    "connection refused",
])
def test_an_ordinary_failure_is_not_read_as_a_token_limit(message: str) -> None:
    assert not is_token_limit_message(message)
    assert classify_design(RuntimeError(message)) is DesignRecoveryError.UNKNOWN


def test_the_policy_lands_the_last_attempt_on_the_maximum() -> None:
    policy = RecoveryPolicy(max_retries=3, max_num_ctx=32768)
    assert policy.num_ctx_for_attempt(1, 8192) == 8192
    assert policy.num_ctx_for_attempt(4, 8192) == 32768
    assert policy.num_ctx_for_attempt(2, 8192) < policy.num_ctx_for_attempt(3, 8192)
