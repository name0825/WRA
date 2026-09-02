import pytest

from wra.core.iteration import IterationEngine
from wra.core.models import ContentConfig, FeatureModel, FrameworkConfig, Section, WRAConfig
from wra.designer.planner import DesignPlanner


def make_config(freedom: str) -> WRAConfig:
    return WRAConfig(
        ollama={"base_url": "http://localhost:11434"},
        models={"analyzer": ["m"], "designer": ["m"], "generator": ["m"]},
        content=ContentConfig(freedom=freedom),
        pipeline={},
    )


def make_engine(freedom: str) -> IterationEngine:
    return IterationEngine(config=make_config(freedom), analyzer=None, designer=None, generator=None, evaluator=None)


def test_default_freedom_is_low() -> None:
    config = WRAConfig(ollama={"base_url": "http://localhost:11434"}, models={}, pipeline={})

    assert config.content.freedom == "low"


def test_invalid_freedom_is_rejected() -> None:
    with pytest.raises(ValueError):
        ContentConfig(freedom="unlimited")


@pytest.mark.parametrize("freedom,coverage_required", [("low", True), ("medium", True), ("high", False), ("ultra", False)])
def test_design_constraints_carry_content_freedom(freedom: str, coverage_required: bool) -> None:
    constraints = make_engine(freedom)._build_design_constraints(1)

    assert constraints["content_freedom"]["level"] == freedom
    assert constraints["content_freedom"]["coverage_required"] is coverage_required
    assert constraints["content_freedom"]["instruction"]


@pytest.mark.parametrize("freedom", ["low", "medium", "high", "ultra"])
def test_generation_rules_carry_content_freedom(freedom: str) -> None:
    rules = make_engine(freedom)._build_generation_rules()

    assert len(rules) == 1
    assert f"Content freedom `{freedom}`" in rules[0]


def test_planner_prompt_labels_coverage_by_freedom() -> None:
    features = FeatureModel(structure={"sections": [Section(tag="section", text="Source section")]})

    strict = DesignPlanner._build_design_context("INSTRUCTIONS", features, {}, None, FrameworkConfig(), "low")
    free = DesignPlanner._build_design_context("INSTRUCTIONS", features, {}, None, FrameworkConfig(), "ultra")

    assert "Coverage required:" in strict
    assert "Coverage required:" not in free
    assert "Content available to place:" in free
