"""Single-model mode: one model and one context window for the whole run."""

import pytest
from pydantic import ValidationError

from wra.core.models import SINGLE_MODEL_ROLES, WRAConfig
from wra.llm.context import estimate_num_ctx
from support import sample_config_data


def _config(**single) -> WRAConfig:
    data = sample_config_data()
    data["models"] = {**data["models"], "single": {"enabled": True, "name": "pinned:26b", **single}}
    return WRAConfig.model_validate(data)


def test_single_mode_broadcasts_one_model_into_every_role() -> None:
    config = _config(think=False, timeout=600)
    for role in SINGLE_MODEL_ROLES:
        pool = getattr(config.models, role)
        assert [spec.name for spec in pool] == ["pinned:26b"]
        assert pool[0].think is False
        assert pool[0].timeout == 600


def test_single_mode_pins_every_recovery_window() -> None:
    config = _config(num_ctx=16384)
    assert config.recovery.initial_num_ctx == 16384
    assert config.recovery.initial_ceiling_num_ctx == 16384
    assert config.recovery.max_num_ctx == 16384


def test_a_pinned_window_is_the_same_size_for_any_prompt() -> None:
    recovery = _config(num_ctx=16384).recovery
    sizes = {
        estimate_num_ctx(prompt, minimum=recovery.initial_num_ctx, maximum=recovery.max_num_ctx, ceiling=recovery.initial_ceiling_num_ctx)
        for prompt in ("hi", "x" * 4_000_000)
    }
    assert sizes == {16384}


def test_an_unpinned_window_leaves_recovery_alone() -> None:
    configured = WRAConfig.model_validate(sample_config_data()).recovery
    recovery = _config(num_ctx=None).recovery
    assert recovery == configured


def test_a_disabled_single_model_leaves_the_pools_alone() -> None:
    data = sample_config_data()
    data["models"] = {**data["models"], "single": {"enabled": False, "name": "pinned:26b"}}
    config = WRAConfig.model_validate(data)
    assert [spec.name for spec in config.models.designer] == [spec["name"] for spec in data["models"]["designer"]]


def test_single_mode_needs_a_model_name() -> None:
    data = sample_config_data()
    data["models"] = {**data["models"], "single": {"enabled": True, "name": "  "}}
    with pytest.raises(ValidationError):
        WRAConfig.model_validate(data)


def test_the_evaluator_pool_is_where_the_evaluator_model_lives() -> None:
    data = sample_config_data()
    data["models"] = {**data["models"], "evaluator": [{"name": "judge:26b", "timeout": 600}]}
    config = WRAConfig.model_validate(data)
    assert [spec.name for spec in config.models.evaluator] == ["judge:26b"]
    assert not hasattr(config, "evaluation")
