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
                "css": {"name": "tailwind", "reason": "modular"},
                "js": {"name": "vanilla-js", "reason": "simple"},
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


def make_evaluation():
    return EvaluationResult(
        overall_score=7.0,
        scores={"content": 7.0},
        strengths=[],
        weaknesses=["Hierarchy is too flat"],
        suggestions=["Increase focal hierarchy"],
    )


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
    assert "Previous DesignSpec" in prompt
    assert "Previous Generated HTML" in prompt
    assert "Evaluation Result" in prompt
    assert "Important schema typing rules" in prompt


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
