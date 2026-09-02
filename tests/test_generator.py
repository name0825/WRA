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
from wra.generator.recovery import HTMLRecoveryError, RecoveryPolicy, classify_exception, classify_response, classify_validation_errors, strategy_for
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
                    ComponentSpec(type="text", purpose="Content", content_source="content.text")
                ],
            )
        ],
        responsive=ResponsiveSpec(strategy="responsive"),
        interaction=InteractionSpec(),
    )


def test_generator_returns_complete_html(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("```html\n<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>\n```")
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt)

    html = generator.generate(FeatureModel(), make_spec())

    assert html.startswith("<!DOCTYPE html>")
    assert "<html>" in html
    assert "</html>" in html
    assert client.calls[0]["options"] == {"num_ctx": 8192}
    assert "format" not in client.calls[0]["options"]


def test_generator_injects_configured_framework_cdn(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>")
    frameworks = FrameworkConfig(
        css=[Framework(name="example-css", cdn="https://example.com/example.css")],
        js=[Framework(name="example-js", cdn="https://example.com/example.js")],
    )
    generator = HTMLGenerator(client, "generator-model", frameworks, prompt)

    html = generator.generate(FeatureModel(), make_spec("example-css", "example-js"))

    assert '<link rel="stylesheet" href="https://example.com/example.css">' in html
    assert '<script src="https://example.com/example.js"></script>' in html


def test_generator_uses_configured_script_tag_for_css_framework(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>")
    frameworks = FrameworkConfig(css=[Framework(name="TailwindCSS", cdn="https://cdn.example/tailwind.js", tag="script")])
    generator = HTMLGenerator(client, "generator-model", frameworks, prompt)

    html = generator.generate(FeatureModel(), make_spec("TailwindCSS"))

    assert '<script src="https://cdn.example/tailwind.js"></script>' in html
    assert '<link rel="stylesheet" href="https://cdn.example/tailwind.js">' not in html


def test_generator_prompt_lists_configured_frameworks_and_loading_tags(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>")
    frameworks = FrameworkConfig(css=[Framework(name="Bootstrap", cdn="https://example.com/bootstrap.css"), Framework(name="TailwindCSS", cdn="https://example.com/tailwind.js", tag="script")], js=[Framework(name="GSAP", cdn="https://example.com/gsap.js", tag="script")])
    generator = HTMLGenerator(client, "generator-model", frameworks, prompt)

    generator.generate(FeatureModel(), make_spec("TailwindCSS"))

    generated_prompt = client.calls[0]["prompt"]
    assert "Available configured frameworks:" in generated_prompt
    assert "Bootstrap" in generated_prompt
    assert "TailwindCSS" in generated_prompt
    assert "HTML loading tag: <script>" in generated_prompt
    assert "GSAP" in generated_prompt
    assert "only configured framework names may be used" in generated_prompt


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
    recovery.write_text("Fix {failure_type}.\n{validation_errors}\nPrevious:\n{previous_response}", encoding="utf-8")
    client = FakeLLMClient(["<div>broken</div>", "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>"])
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt, recovery, RecoveryPolicy(max_retries=2))

    html = generator.generate(FeatureModel(), make_spec())

    assert "<html>" in html
    assert len(client.calls) == 2
    assert "broken" in client.calls[1]["prompt"]


def test_generator_increases_context_on_token_error_without_previous_response(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    recovery = tmp_path / "recovery.md"
    recovery.write_text("Fix {failure_type}. Previous: {previous_response}", encoding="utf-8")

    class TokenThenSuccess(FakeLLMClient):
        def generate(self, prompt: str, model: str, options=None) -> str:
            self.calls.append({"prompt": prompt, "model": model, "options": options or {}})
            if len(self.calls) == 1:
                raise RuntimeError("maximum context length exceeded")
            return "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>"

    client = TokenThenSuccess([])
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt, recovery, RecoveryPolicy(max_retries=2, initial_num_ctx=100, max_num_ctx=400, context_multiplier=2))

    generator.generate(FeatureModel(), make_spec())

    assert client.calls[0]["options"]["num_ctx"] == 100
    assert client.calls[1]["options"]["num_ctx"] == 200
    assert "maximum context length exceeded" not in client.calls[1]["prompt"]
    assert "Previous:" not in client.calls[1]["prompt"]


def test_generator_increases_context_when_html_response_is_truncated(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    recovery = tmp_path / "recovery.md"
    recovery.write_text("Fix {failure_type}. Previous: {previous_response}", encoding="utf-8")
    truncated = "<!DOCTYPE html><html><head><style>body { background: #111; color: #fff; } .panel { padding: 20px;"
    complete = "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head><body></body></html>"
    client = FakeLLMClient([truncated, complete])
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt, recovery, RecoveryPolicy(max_retries=2, initial_num_ctx=100, max_num_ctx=400, context_multiplier=2))

    generator.generate(FeatureModel(), make_spec())

    assert client.calls[0]["options"]["num_ctx"] == 100
    assert client.calls[1]["options"]["num_ctx"] == 200
    assert "token_limit" in client.calls[1]["prompt"]
    assert truncated not in client.calls[1]["prompt"]


def test_recovery_detects_truncated_html_as_token_limit():
    truncated = "<!DOCTYPE html><html><head><style>body { background: #111; color: #fff;"
    assert classify_response(truncated) is HTMLRecoveryError.TOKEN_LIMIT
    assert classify_exception(GeneratorError("Generator LLM did not return a complete HTML document"), response=truncated) is HTMLRecoveryError.TOKEN_LIMIT


def test_recovery_keeps_short_malformed_html_as_structure_error():
    malformed = "<html><body><div>broken"
    assert classify_response(malformed) is HTMLRecoveryError.UNKNOWN
    assert classify_exception(GeneratorError("Generator LLM did not return a complete HTML document"), response=malformed) is HTMLRecoveryError.STRUCTURE


def test_recovery_classifies_content_and_structure_failures():
    assert classify_validation_errors(["missing required content: title"]) is HTMLRecoveryError.CONTENT
    assert classify_validation_errors(["duplicate id: hero"]) is HTMLRecoveryError.STRUCTURE
    assert strategy_for(HTMLRecoveryError.TOKEN_LIMIT).value == "increase_context"
    assert strategy_for(HTMLRecoveryError.CONTENT).value == "revise_response"
    assert strategy_for(HTMLRecoveryError.UNKNOWN).value == "regenerate"
