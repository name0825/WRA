import pytest

from wra.core.models import FeatureModel, Framework, FrameworkConfig
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
from wra.generator.generator import GeneratorError, HTMLGenerator
from wra.generator.recovery import HTMLRecoveryError, RecoveryPolicy, classify_validation_errors, strategy_for
from wra.llm.client import LLMClient


class FakeLLMClient(LLMClient):
    def __init__(self, responses: list[str] | str, error: Exception | None = None) -> None:
        self.responses = [responses] if isinstance(responses, str) else responses
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None) -> str:
        self.calls.append({"prompt": prompt, "model": model, "options": options or {}})
        if self.error is not None:
            raise self.error
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def make_spec(css_name="none", js_name="none") -> DesignSpec:
    return DesignSpec(
        design=Design(
            concept="Test",
            visual_direction="Minimal",
            layout="Single column",
            color_system=ColorSystem(
                background="#fff",
                surface="#f5f5f5",
                text="#111",
                muted_text="#666",
                primary="#000",
                accent="#333",
            ),
            typography=Typography(
                heading="sans-serif",
                body="sans-serif",
                scale="standard",
                weight="400",
            ),
            framework=FrameworkRecommendations(
                css=FrameworkRecommendation(name=css_name, reason="test"),
                js=FrameworkRecommendation(name=js_name, reason="test"),
            ),
            motion=Motion(level="none"),
        ),
        sections=[
            SectionSpec(
                id="main",
                purpose="Main content",
                layout="single",
                components=[
                    ComponentSpec(
                        type="text",
                        purpose="Content",
                        content_source="content.text",
                    )
                ],
            )
        ],
        responsive=ResponsiveSpec(strategy="responsive"),
        interaction=InteractionSpec(),
    )


def test_generator_returns_complete_html(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("```html\n<!DOCTYPE html><html><head></head><body></body></html>\n```")
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt)

    html = generator.generate(FeatureModel(), make_spec())

    assert html.startswith("<!DOCTYPE html>")
    assert "<html>" in html
    assert "</html>" in html


def test_generator_injects_configured_framework_cdn(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("<!DOCTYPE html><html><head></head><body></body></html>")
    frameworks = FrameworkConfig(
        css=[Framework(name="example-css", cdn="https://example.com/example.css")],
        js=[Framework(name="example-js", cdn="https://example.com/example.js")],
    )
    generator = HTMLGenerator(client, "generator-model", frameworks, prompt)

    html = generator.generate(FeatureModel(), make_spec("example-css", "example-js"))

    assert '<link rel="stylesheet" href="https://example.com/example.css">' in html
    assert '<script src="https://example.com/example.js"></script>' in html


def test_generator_rejects_unconfigured_framework(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("<!DOCTYPE html><html><head></head><body></body></html>")
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt)

    with pytest.raises(GeneratorError, match="not configured"):
        generator.generate(FeatureModel(), make_spec("unknown-css"))


def test_generator_rejects_incomplete_html(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("<html output>", encoding="utf-8")
    client = FakeLLMClient("<div>not a document</div>")
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt)

    with pytest.raises(GeneratorError, match="HTML generation failed"):
        generator.generate(FeatureModel(), make_spec())


def test_generator_recovers_from_structure_error_using_previous_response(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    recovery = tmp_path / "recovery.md"
    recovery.write_text(
        "Fix {failure_type}.\n{validation_errors}\nPrevious:\n{previous_response}",
        encoding="utf-8",
    )
    client = FakeLLMClient([
        "<div>broken</div>",
        "<!DOCTYPE html><html><head></head><body></body></html>",
    ])
    generator = HTMLGenerator(
        client,
        "generator-model",
        FrameworkConfig(),
        prompt,
        recovery,
        RecoveryPolicy(max_retries=2),
    )

    html = generator.generate(FeatureModel(), make_spec())

    assert "<html>" in html
    assert len(client.calls) == 2
    assert "broken" in client.calls[1]["prompt"]


def test_generator_increases_context_on_token_error(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    recovery = tmp_path / "recovery.md"
    recovery.write_text("Fix {failure_type}. Previous: {previous_response}", encoding="utf-8")

    class TokenThenSuccess(FakeLLMClient):
        def generate(self, prompt: str, model: str, options=None) -> str:
            self.calls.append({"prompt": prompt, "model": model, "options": options or {}})
            if len(self.calls) == 1:
                raise RuntimeError("maximum context length exceeded")
            return "<!DOCTYPE html><html><head></head><body></body></html>"

    client = TokenThenSuccess([])
    generator = HTMLGenerator(
        client,
        "generator-model",
        FrameworkConfig(),
        prompt,
        recovery,
        RecoveryPolicy(max_retries=2, initial_num_ctx=100, max_num_ctx=400, context_multiplier=2),
    )

    generator.generate(FeatureModel(), make_spec())

    assert client.calls[0]["options"]["num_ctx"] == 100
    assert client.calls[1]["options"]["num_ctx"] == 200


def test_recovery_classifies_content_and_structure_failures():
    assert classify_validation_errors(["missing required content: title"]) is HTMLRecoveryError.CONTENT
    assert classify_validation_errors(["duplicate id: hero"]) is HTMLRecoveryError.STRUCTURE
    assert strategy_for(HTMLRecoveryError.TOKEN_LIMIT).value == "increase_context"
    assert strategy_for(HTMLRecoveryError.CONTENT).value == "revise_response"
    assert strategy_for(HTMLRecoveryError.UNKNOWN).value == "regenerate"
