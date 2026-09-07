import logging

import pytest

from wra.core.models import FeatureModel
from wra.designer.schema import (
    ColorSystem,
    ComponentSpec,
    Design,
    DesignSpec,
    FrameworkRecommendation,
    FrameworkRecommendations,
    InteractionSpec,
    Motion,
    ResponsiveSpec,
    SectionSpec,
    Typography,
)
from wra.evaluator.llm import LLMEvaluator
from wra.llm.client import LLMClient
from wra.llm import context as context_module

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


def _evaluation_response() -> str:
    import json

    payload = {name: {"score": 8, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["overall_score"] = 8
    payload["summary"] = "Solid."
    payload["improvement_suggestions"] = []
    return json.dumps(payload)


def make_spec() -> DesignSpec:
    return DesignSpec(
        design=Design(
            concept="Test",
            visual_direction="Minimal",
            layout="Single column",
            color_system=ColorSystem(background="#fff", surface="#f5f5f5", text="#111", muted_text="#666", primary="#000", accent="#333"),
            typography=Typography(heading="sans-serif", body="sans-serif", scale="standard", weight="400"),
            framework=FrameworkRecommendations(css=FrameworkRecommendation(name="none", reason="test"), js=FrameworkRecommendation(name="none", reason="test")),
            motion=Motion(level="none"),
        ),
        sections=[SectionSpec(id="main", purpose="Main content", layout="single", components=[ComponentSpec(type="text", purpose="Content", content_source="content.text")])],
        responsive=ResponsiveSpec(strategy="responsive"),
        interaction=InteractionSpec(),
    )


class FakeLLMClient(LLMClient):
    """Raises once, then succeeds; records each call's options."""

    def __init__(self, error: Exception, response: str) -> None:
        self.error = error
        self.response = response
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "options": options or {}})
        if len(self.calls) == 1:
            raise self.error
        return self.response


def _evaluator_retry(tmp_path, error: Exception):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FakeLLMClient(error, _evaluation_response())
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=1000, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)
    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>")
    assert result.overall_score == 8
    assert len(client.calls) == 2
    return client


def test_evaluator_grows_num_ctx_on_a_token_limit_retry(tmp_path):
    client = _evaluator_retry(tmp_path, RuntimeError("LLM generation stopped due to output token limit"))

    first_num_ctx = client.calls[0]["options"]["num_ctx"]
    second_num_ctx = client.calls[1]["options"]["num_ctx"]
    assert second_num_ctx > first_num_ctx
    assert second_num_ctx == 32768


def test_evaluator_holds_num_ctx_on_a_schema_retry(tmp_path):
    """A larger window does not repair a wrong shape, and costs VRAM to ask for."""
    client = _evaluator_retry(tmp_path, RuntimeError("schema mismatch"))

    assert client.calls[1]["options"]["num_ctx"] == client.calls[0]["options"]["num_ctx"]


def test_clamp_warns_when_the_ceiling_binds(caplog):
    caplog.set_level(logging.WARNING, logger="wra.llm.context")
    context_module.estimate_num_ctx("x" * 100000, 1024, 32768, ceiling=2048)
    assert "num_ctx clamped" in caplog.text


def test_no_clamp_warning_when_the_prompt_fits(caplog):
    caplog.set_level(logging.WARNING, logger="wra.llm.context")
    context_module.estimate_num_ctx("short prompt", 1024, 32768, ceiling=32768)
    assert "num_ctx clamped" not in caplog.text
