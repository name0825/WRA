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
from wra.llm.client import LLMClient


class FakeLLMClient(LLMClient):
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, prompt: str, model: str, options=None) -> str:
        return self.response


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
    prompt.write_text("Generate HTML.", encoding="utf-8")
    client = FakeLLMClient("<div>not a document</div>")
    generator = HTMLGenerator(client, "generator-model", FrameworkConfig(), prompt)

    with pytest.raises(GeneratorError, match="complete HTML document"):
        generator.generate(FeatureModel(), make_spec())
