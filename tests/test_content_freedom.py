import pytest

from wra.core.iteration import IterationEngine
from wra.core.models import FACTUAL_ACCURACY_CAP, FACTUAL_ACCURACY_RULE, ContentConfig, FeatureModel, FrameworkConfig, Section, WRAConfig, content_freedom_policy
from wra.designer.planner import DesignPlanner
from wra.evaluator.llm import EVALUATION_SCHEMA, LLMEvaluator


def make_config(freedom: str) -> WRAConfig:
    return WRAConfig(
        ollama={"base_url": "http://localhost:11434"},
        models={"analyzer": ["m"], "designer": ["m"], "generator": ["m"]},
        content=ContentConfig(freedom=freedom),
        pipeline={},
    )


def make_engine(freedom: str) -> IterationEngine:
    return IterationEngine(config=make_config(freedom), designer=None, generator=None, evaluator=None)


def test_default_freedom_is_low() -> None:
    config = WRAConfig(ollama={"base_url": "http://localhost:11434"}, models={}, pipeline={})

    assert config.content.freedom == "low"


def test_invalid_freedom_is_rejected() -> None:
    with pytest.raises(ValueError):
        ContentConfig(freedom="unlimited")


@pytest.mark.parametrize("freedom,coverage_required", [("low", True), ("medium", True), ("high", False), ("ultra", False), ("madmax", False)])
def test_design_constraints_carry_content_freedom(freedom: str, coverage_required: bool) -> None:
    constraints = make_engine(freedom)._build_design_constraints(1)

    assert constraints["content_freedom"]["level"] == freedom
    assert constraints["content_freedom"]["coverage_required"] is coverage_required
    assert constraints["content_freedom"]["instruction"]


@pytest.mark.parametrize("freedom", ["low", "medium", "high", "ultra", "madmax"])
def test_generation_rules_carry_content_freedom(freedom: str) -> None:
    rules = make_engine(freedom)._build_generation_rules()

    assert f"Content freedom `{freedom}`" in rules[0]
    assert content_freedom_policy(freedom)["authoring"] in rules[0]


def test_planner_prompt_labels_coverage_by_freedom() -> None:
    features = FeatureModel(structure={"sections": [Section(tag="section", text="Source section")]})

    strict = DesignPlanner._build_design_context("INSTRUCTIONS", features, {}, None, FrameworkConfig(), "low")
    free = DesignPlanner._build_design_context("INSTRUCTIONS", features, {}, None, FrameworkConfig(), "ultra")

    assert "Coverage required:" in strict
    assert "Coverage required:" not in free
    assert "Content available to place:" in free


@pytest.mark.parametrize("freedom", ["low", "medium", "high", "ultra", "madmax"])
def test_factual_accuracy_rule_reaches_designer_and_generator(freedom: str) -> None:
    engine = make_engine(freedom)

    assert engine._build_design_constraints(1)["content_freedom"]["factual_accuracy"] == FACTUAL_ACCURACY_RULE
    assert any(FACTUAL_ACCURACY_RULE in rule for rule in engine._build_generation_rules())


@pytest.mark.parametrize("freedom", ["low", "medium", "high", "ultra", "madmax"])
def test_evaluator_instruction_carries_level_and_factual_cap(freedom: str) -> None:
    evaluator = LLMEvaluator(llm_client=None, model="m", content_freedom=freedom)
    instruction = evaluator._content_freedom_instruction()

    assert f"`{freedom}`" in instruction
    assert content_freedom_policy(freedom)["evaluation"] in instruction
    assert FACTUAL_ACCURACY_CAP in instruction


def test_the_evaluation_schema_does_not_ask_the_model_for_the_freedom_level() -> None:
    # content_freedom is injected by the evaluator after parsing; constraining
    # the model to report it would let it contradict the pipeline.
    assert "content_freedom" not in EVALUATION_SCHEMA["properties"]
    assert "content_freedom" not in EVALUATION_SCHEMA["required"]
    assert "overall_score" in EVALUATION_SCHEMA["required"]


def test_every_freedom_level_has_a_complete_policy() -> None:
    for level in ("low", "medium", "high", "ultra", "madmax"):
        policy = content_freedom_policy(level)

        assert set(policy) == {"authoring", "planning", "evaluation"}
        assert all(policy.values())


_LOWER_LEVEL_POLICY_DIGESTS = {
    "low": "5d9de786cc1fd2f5",
    "medium": "1a5c0ba631c463a1",
    "high": "4a06dac0811798c0",
    "ultra": "66577aa61e5b0c2a",
}


@pytest.mark.parametrize("level", list(_LOWER_LEVEL_POLICY_DIGESTS))
def test_the_lower_levels_policy_strings_are_unchanged(level: str) -> None:
    import hashlib

    digest = hashlib.sha256(repr(sorted(content_freedom_policy(level).items())).encode()).hexdigest()[:16]

    assert digest == _LOWER_LEVEL_POLICY_DIGESTS[level]


def test_madmax_allows_qualitative_content_but_not_placeholders_or_fake_submission() -> None:
    policy = content_freedom_policy("madmax")

    for text in (policy["authoring"], policy["planning"]):
        assert "[대표자명]" in text or "placeholder" in text
        assert "contact" in text
    assert "icon" in policy["authoring"] and "contact form" in policy["authoring"]
    assert "business.phone" in policy["authoring"]
    assert "no success banner" in policy["authoring"]
    assert "never fakes a successful submission" in policy["planning"]
    for text in (policy["authoring"], policy["planning"], policy["evaluation"]):
        assert "quality and service claims" in text.lower()


@pytest.mark.parametrize("prompt", ["generator", "designer"])
def test_generator_and_designer_prompts_render_contact_facts_without_faking_submission(prompt: str) -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "prompts" / prompt / "system.md").read_text(encoding="utf-8")

    assert "business.phone" in text
    assert "fake submission" in text
