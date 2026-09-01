import json

import pytest

from wra.core.models import FeatureModel
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import DesignRecoveryError, RecoveryPolicy, build_recovery_prompt
from wra.validator.design import validate_design_spec


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, prompt, model, options=None):
        self.calls.append({"prompt": prompt, "model": model, "options": options})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_design_spec() -> dict:
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
            "typography": {
                "heading": "Inter",
                "body": "Inter",
                "scale": "large",
                "weight": "mixed",
            },
            "framework": {
                "css": {"name": "tailwind", "reason": "modular"},
                "js": {"name": "vanilla-js", "reason": "simple"},
            },
            "motion": {"level": "mid", "principles": ["subtle reveal"]},
        },
        "sections": [
            {
                "id": "featured",
                "purpose": "Present content",
                "layout": "grid",
                "components": [
                    {
                        "type": "card",
                        "purpose": "Display content",
                        "content_source": "content.text",
                    }
                ],
            }
        ],
        "responsive": {
            "strategy": "desktop-first",
            "breakpoints": [{"name": "mobile", "width": "480px"}],
            "behavior": ["single column"],
        },
        "interaction": {
            "principles": ["subtle transitions"],
            "components": [{"target": "card", "behavior": "hover"}],
        },
    }


def test_design_validator_rejects_duplicate_sections_and_bad_references():
    data = make_design_spec()
    data["sections"].append(data["sections"][0].copy())
    data["sections"][1]["id"] = "featured"
    data["sections"][0]["components"][0]["content_source"] = "content.unknown"
    data["interaction"]["components"][0]["target"] = "missing"

    from wra.designer.schema import DesignSpec

    result = validate_design_spec(DesignSpec.model_validate(data), FeatureModel())

    assert not result.valid
    assert "duplicate section id: featured" in result.errors
    assert "invalid content_source: content.unknown" in result.errors
    assert "invalid interaction target: missing" in result.errors


def test_token_limit_increases_num_ctx_and_reuses_previous_response():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient([
        RuntimeError("maximum context length exceeded"),
        valid,
    ])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(
            max_retries=2,
            initial_num_ctx=4096,
            max_num_ctx=16384,
            context_multiplier=2,
        ),
    )

    result = planner.plan(FeatureModel())

    assert result.design.concept == "Editorial Minimal"
    assert client.calls[0]["options"]["num_ctx"] == 4096
    assert client.calls[1]["options"]["num_ctx"] == 8192
    assert "Failure type: token_limit" in client.calls[1]["prompt"]


def test_invalid_json_revises_previous_response():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient(["{broken", valid])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=1),
    )

    planner.plan(FeatureModel())

    assert "Previous response:\n{broken" in client.calls[1]["prompt"]
    assert "Failure type: json_structure" in client.calls[1]["prompt"]
    assert "DesignSpec required structure:" in client.calls[1]["prompt"]


def test_invalid_json_recovery_does_not_duplicate_original_prompt():
    prompt = "Feature Model: " + ("x" * 30000)
    previous = "{" + ("y" * 12000)
    recovery = build_recovery_prompt(
        prompt,
        previous,
        DesignRecoveryError.JSON_STRUCTURE,
        ["Designer LLM returned invalid JSON"],
        "",
    )

    assert len(recovery) < len(prompt) + len(previous)
    assert prompt not in recovery
    assert previous in recovery


def test_unknown_error_regenerates_without_previous_response():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient([RuntimeError("connection reset"), valid])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=1),
    )

    planner.plan(FeatureModel())

    assert client.calls[1]["prompt"] == client.calls[0]["prompt"]


def test_recovery_stops_at_max_num_ctx_and_regenerates():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient([
        RuntimeError("context length exceeded"),
        RuntimeError("context length exceeded"),
        valid,
    ])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(
            max_retries=2,
            initial_num_ctx=8192,
            max_num_ctx=8192,
            context_multiplier=2,
        ),
    )

    planner.plan(FeatureModel())

    assert client.calls[1]["options"]["num_ctx"] == 8192
    assert client.calls[2]["prompt"] == client.calls[0]["prompt"]


def test_recovery_raises_after_retry_limit():
    client = FakeLLMClient([RuntimeError("connection reset")] * 3)
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=2),
    )

    with pytest.raises(RuntimeError, match="failed after 2 retries"):
        planner.plan(FeatureModel())
