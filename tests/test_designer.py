import json

import pytest

from wra.core.models import FeatureModel
from wra.designer.planner import DesignPlanner


class FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, prompt, model, options=None):
        self.calls.append({"prompt": prompt, "model": model, "options": options})
        return self.response


def make_design_spec() -> dict:
    return {
        "design": {
            "concept": "Editorial Minimal",
            "visual_direction": "Typography-focused editorial layout",
            "layout": "asymmetric",
            "color_system": {
                "background": "#ffffff",
                "surface": "#f5f5f5",
                "text": "#111111",
                "muted_text": "#666666",
                "primary": "#111111",
                "accent": "#ff5a36",
            },
            "typography": {
                "heading": "Inter",
                "body": "Inter",
                "scale": "large",
                "weight": "mixed",
            },
            "framework": {
                "css": {
                    "name": "tailwind",
                    "reason": "Suitable for modular layouts",
                },
                "js": {
                    "name": "vanilla-js",
                    "reason": "No complex JavaScript framework is required",
                },
            },
            "motion": {
                "level": "mid",
                "principles": ["subtle section reveal"],
            },
        },
        "sections": [
            {
                "id": "featured-content",
                "purpose": "Present important content",
                "layout": "asymmetric-grid",
                "components": [
                    {
                        "type": "card",
                        "purpose": "Display featured content",
                        "content_source": "content.text",
                    }
                ],
            }
        ],
        "responsive": {
            "strategy": "desktop-first",
            "breakpoints": [{"name": "mobile", "width": "480px"}],
            "behavior": ["grid becomes single column"],
        },
        "interaction": {
            "principles": ["subtle transitions"],
            "components": [{"target": "card", "behavior": "hover reveal"}],
        },
    }


def test_design_spec_supports_component_content_source_and_motion() -> None:
    client = FakeLLMClient(json.dumps(make_design_spec()))
    planner = DesignPlanner(client, "designer-model")

    result = planner.plan(FeatureModel())

    assert result.sections[0].components[0].content_source == "content.text"
    assert result.design.motion.level == "mid"
    assert result.design.framework.css.name == "tailwind"
    assert result.design.framework.js.name == "vanilla-js"


def test_design_spec_rejects_invalid_motion_level() -> None:
    data = make_design_spec()
    data["design"]["motion"]["level"] = "extreme"
    client = FakeLLMClient(json.dumps(data))
    planner = DesignPlanner(client, "designer-model")

    with pytest.raises(ValueError, match="does not match DesignSpec"):
        planner.plan(FeatureModel())


def test_planner_passes_constraints_and_rules_to_llm() -> None:
    client = FakeLLMClient(json.dumps(make_design_spec()))
    planner = DesignPlanner(client, "designer-model")

    planner.plan(
        FeatureModel(),
        design_constraints={"theme": "dark"},
        generation_rules=["preserve required content"],
    )

    call = client.calls[0]
    assert call["model"] == "designer-model"
    assert call["options"] == {"format": "json"}
    assert '"theme": "dark"' in call["prompt"]
    assert "preserve required content" in call["prompt"]


def test_planner_rejects_invalid_json() -> None:
    client = FakeLLMClient("not json")
    planner = DesignPlanner(client, "designer-model")

    with pytest.raises(ValueError, match="invalid JSON"):
        planner.plan(FeatureModel())
