import json
import re
from pathlib import Path

from wra.core.models import FeatureModel, FrameworkConfig
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient


class GeneratorError(ValueError):
    """Raised when a DesignSpec cannot be converted into HTML."""


class HTMLGenerator:
    """Generate a complete HTML document from a FeatureModel and DesignSpec."""

    def __init__(
        self,
        llm_client: LLMClient,
        generator_model: str,
        frameworks: FrameworkConfig,
        prompt_path: str | Path = "prompts/generator/system.md",
    ) -> None:
        self._llm_client = llm_client
        self._generator_model = generator_model
        self._frameworks = frameworks
        self._prompt_path = Path(prompt_path)

    def generate(
        self,
        features: FeatureModel,
        design_spec: DesignSpec,
        generation_rules: list[str] | None = None,
    ) -> str:
        cdn_tags = self._build_framework_tags(design_spec)
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        prompt = (
            f"{prompt_template}\n\n"
            "Feature Model:\n"
            f"{features.model_dump_json(indent=2)}\n\n"
            "DesignSpec:\n"
            f"{design_spec.model_dump_json(indent=2)}\n\n"
            "Generation Rules:\n"
            f"{json.dumps(generation_rules or [], ensure_ascii=False, indent=2)}\n\n"
            "Framework CDN dependencies to include exactly:\n"
            f"{cdn_tags or '(none)'}"
        )

        response = self._llm_client.generate(
            prompt=prompt,
            model=self._generator_model,
            options={},
        )
        html = self._extract_html(response)
        return self._inject_framework_tags(html, cdn_tags)

    def _build_framework_tags(self, design_spec: DesignSpec) -> str:
        tags: list[str] = []

        css_name = design_spec.design.framework.css.name.strip()
        if css_name and css_name.lower() not in {"none", "vanilla css", "vanilla"}:
            framework = self._frameworks.find_css(css_name)
            if framework is None:
                raise GeneratorError(f"CSS framework is not configured: {css_name}")
            tags.append(f'<link rel="stylesheet" href="{framework.cdn}">')

        js_name = design_spec.design.framework.js.name.strip()
        if js_name and js_name.lower() not in {"none", "vanilla javascript", "vanilla js", "vanilla"}:
            framework = self._frameworks.find_js(js_name)
            if framework is None:
                raise GeneratorError(f"JavaScript framework is not configured: {js_name}")
            tags.append(f'<script src="{framework.cdn}"></script>')

        return "\n".join(tags)

    @staticmethod
    def _extract_html(response: str) -> str:
        text = response.strip()
        fenced = re.fullmatch(
            r"```(?:html)?\s*(.*?)\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced:
            text = fenced.group(1).strip()

        start = text.lower().find("<!doctype html>")
        if start >= 0:
            text = text[start:]
        else:
            start = text.lower().find("<html")
            if start >= 0:
                text = text[start:]

        if "<html" not in text.lower() or "</html>" not in text.lower():
            raise GeneratorError("Generator LLM did not return a complete HTML document")

        end = text.lower().rfind("</html>") + len("</html>")
        return text[:end].strip()

    @staticmethod
    def _inject_framework_tags(html: str, cdn_tags: str) -> str:
        if not cdn_tags:
            return html

        match = re.search(r"</head>", html, flags=re.IGNORECASE)
        if match is None:
            raise GeneratorError("Generated HTML does not contain a closing </head> tag")

        return f"{html[:match.start()]}{cdn_tags}\n{html[match.start():]}"
