import json

from wra.core.models import EvaluationResult, FeatureModel
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import DesignRecoveryError, build_recovery_prompt
from wra.designer.schema import DesignSpec


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, prompt, model, options=None, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, "options": options, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_design_spec():
    return {
        "design": {
            "concept": "Editorial Minimal",
            "visual_direction": "Typography-focused editorial layout",
            "layout": "asymmetric",
            "color_system": {
                "background": "#fff",
                "surface": "#f5f5f5",
                "text": "#111",
                "muted_text": "#666",
                "primary": "#111",
                "accent": "#f50",
            },
            "typography": {"heading": "Inter", "body": "Inter", "scale": "large", "weight": "mixed"},
            "framework": {
                "css": {"name": "none", "reason": "not needed"},
                "js": {"name": "none", "reason": "not needed"},
            },
            "motion": {"level": "mid", "principles": ["subtle reveal"]},
        },
        "sections": [{
            "id": "featured",
            "purpose": "Present content",
            "layout": "grid",
            "components": [{"type": "card", "purpose": "Display content", "content_source": "content.text"}],
        }],
        "responsive": {"strategy": "desktop-first", "breakpoints": [{"name": "mobile", "width": "480px"}], "behavior": ["single column"]},
        "interaction": {"principles": ["subtle transitions"], "components": [{"target": "card", "behavior": "hover"}]},
    }


DIMENSIONS = (
    "design_completeness",
    "visual_hierarchy",
    "design_consistency",
    "content_preservation",
    "ux",
    "readability",
    "responsive_design",
    "originality",
    "html_quality",
)


def make_evaluation():
    payload = {name: {"score": 7.0, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["overall_score"] = 7.0
    payload["summary"] = "Hierarchy is too flat"
    payload["improvement_suggestions"] = ["Increase focal hierarchy"]
    return EvaluationResult.model_validate(payload)


def test_revise_prompt_preserves_full_design_context():
    client = FakeLLMClient([json.dumps(make_design_spec())])
    planner = DesignPlanner(client, "designer-model")
    features = FeatureModel()

    planner.revise(features, DesignSpec.model_validate(make_design_spec()), "<html>previous</html>", make_evaluation())

    prompt = client.calls[0]["prompt"]
    assert "# WRA Design Planner" in prompt
    assert "Feature Model:" in prompt
    assert "Available Feature Model reference paths:" in prompt
    assert "`metadata`" in prompt
    assert "`content.text`" in prompt
    assert "Coverage required:" in prompt
    assert "Previous DesignSpec" in prompt
    assert "Previous HTML evidence" in prompt
    assert "Evaluation Result" in prompt
    assert "WRA DesignSpec Revision" in prompt
    # The revision rules must land after the evidence so prompt truncation,
    # which drops the front of the prompt, cannot remove them.
    assert prompt.index("Previous DesignSpec") < prompt.index("## Revision depth")


def test_revise_prompt_turns_measured_overflow_into_a_responsive_correction():
    client = FakeLLMClient([json.dumps(make_design_spec())])
    planner = DesignPlanner(client, "designer-model")
    evaluation = make_evaluation()
    evaluation.responsive_design.score = 4.0
    evaluation.responsive_design.feedback = "Mobile horizontal_overflow at width 390: scroll_width 511."
    evaluation.improvement_suggestions = ["Contain the testimonial marquee at 390px."]

    planner.revise(FeatureModel(), DesignSpec.model_validate(make_design_spec()), "<html>previous</html>", evaluation)

    prompt = client.calls[0]["prompt"]
    assert "Mobile horizontal_overflow at width 390: scroll_width 511." in prompt
    assert "Contain the testimonial marquee at 390px." in prompt
    assert prompt.index("Mobile horizontal_overflow") < prompt.index("When `responsive_design` feedback reports measured `horizontal_overflow`")


def test_revise_prompt_uses_actual_feature_model_reference_paths():
    features = FeatureModel.model_validate({
        "metadata": {"title": "Original title"},
        "content": {"text": ["Original body"]},
    })
    client = FakeLLMClient([json.dumps(make_design_spec())])
    planner = DesignPlanner(client, "designer-model")

    planner.revise(features, DesignSpec.model_validate(make_design_spec()), "<html></html>", make_evaluation())

    prompt = client.calls[0]["prompt"]
    assert "Original title" in prompt
    assert "Original body" in prompt
    assert "`metadata.title`" in prompt
    assert "`content.text`" in prompt


def test_revision_recovery_includes_feature_model_and_reference_paths():
    features = FeatureModel.model_validate({"metadata": {"title": "Original title"}, "content": {"text": ["Original body"]}})
    planner = DesignPlanner(FakeLLMClient(["{broken", json.dumps(make_design_spec())]), "designer-model")

    planner.revise(features, DesignSpec.model_validate(make_design_spec()), "<html></html>", make_evaluation())

    recovery_prompt = planner._llm_client.calls[1]["prompt"]
    assert "Original title" in recovery_prompt
    assert "Original body" in recovery_prompt
    assert "`metadata.title`" in recovery_prompt
    assert "`content.text`" in recovery_prompt
    assert "DesignSpec required structure:" in recovery_prompt


def test_build_recovery_prompt_does_not_require_original_prompt_for_context():
    recovery = build_recovery_prompt(
        "REVISION PROMPT WITHOUT FEATURE DATA",
        "{broken",
        DesignRecoveryError.JSON_STRUCTURE,
        ["invalid JSON"],
        recovery_context="Feature Model:\n{\"metadata\": {\"title\": \"Original title\"}}\n\nAvailable Feature Model reference paths:\n- `metadata.title`",
    )

    assert "Original title" in recovery
    assert "`metadata.title`" in recovery
    assert "REVISION PROMPT WITHOUT FEATURE DATA" not in recovery
