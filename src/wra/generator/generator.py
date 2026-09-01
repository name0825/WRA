import json
import re
from pathlib import Path
from typing import Any

from wra.core.models import FeatureModel, FrameworkConfig
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient
from wra.validator.html import validate_html
from .recovery import HTMLRecoveryError, RecoveryPolicy, RecoveryStrategy, build_recovery_prompt, classify_exception, classify_validation_errors, strategy_for


class GeneratorError(ValueError):
    """Raised when a DesignSpec cannot be converted into valid HTML."""


class HTMLGenerator:
    """Generate and recover a complete HTML document from a FeatureModel and DesignSpec."""

    def __init__(self, llm_client: LLMClient, generator_model: str, frameworks: FrameworkConfig, prompt_path: str | Path = "prompts/generator/system.md", recovery_prompt_path: str | Path = "prompts/generator/recovery.md", recovery_policy: RecoveryPolicy | None = None) -> None:
        self._llm_client = llm_client
        self._generator_model = generator_model
        self._frameworks = frameworks
        self._prompt_path = Path(prompt_path)
        self._recovery_prompt_path = Path(recovery_prompt_path)
        self._recovery_policy = recovery_policy or RecoveryPolicy()

    def generate(self, features: FeatureModel, design_spec: DesignSpec, generation_rules: list[str] | None = None, model: str | None = None) -> str:
        cdn_tags = self._build_framework_tags(design_spec)
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        revision_template = self._recovery_prompt_path.read_text(encoding="utf-8")
        original_prompt = self._build_prompt(prompt_template, features, design_spec, generation_rules, cdn_tags)
        prompt = original_prompt
        previous_response = None
        num_ctx = self._recovery_policy.initial_num_ctx
        last_error = None
        selected_model = model or self._generator_model

        for attempt in range(self._recovery_policy.max_retries + 1):
            try:
                response = self._llm_client.generate(prompt=prompt, model=selected_model, options={"num_ctx": num_ctx})
                previous_response = response
                html = self._extract_html(response)
                html = self._inject_framework_tags(html, cdn_tags)
                validation = validate_html(html, features)
                if validation.valid:
                    return html
                error = classify_validation_errors(validation.errors)
                last_error = "; ".join(validation.errors)
                if attempt >= self._recovery_policy.max_retries:
                    break
                strategy = strategy_for(error)
                prompt = build_recovery_prompt(original_prompt, previous_response, error, validation.errors, revision_template)
                if strategy is RecoveryStrategy.INCREASE_CONTEXT:
                    num_ctx = self._recovery_policy.next_num_ctx(num_ctx)
                continue
            except Exception as exc:
                error = classify_exception(exc)
                last_error = str(exc)
                if attempt >= self._recovery_policy.max_retries:
                    break
                strategy = strategy_for(error)
                if strategy is RecoveryStrategy.INCREASE_CONTEXT:
                    num_ctx = self._recovery_policy.next_num_ctx(num_ctx)
                prompt = build_recovery_prompt(original_prompt, previous_response, error, [str(exc)], revision_template)

        raise GeneratorError(f"HTML generation failed after {self._recovery_policy.max_retries + 1} attempts" + (f": {last_error}" if last_error else ""))

    @staticmethod
    def _build_prompt(prompt_template, features, design_spec, generation_rules, cdn_tags):
        return f"{prompt_template}\n\nFeature Model:\n{features.model_dump_json(indent=2)}\n\nDesignSpec:\n{design_spec.model_dump_json(indent=2)}\n\nGeneration Rules:\n{json.dumps(generation_rules or [], ensure_ascii=False, indent=2)}\n\nFramework CDN dependencies to include exactly:\n{cdn_tags or '(none)'}"

    def _build_framework_tags(self, design_spec: DesignSpec) -> str:
        tags = []
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
        fenced = re.fullmatch(r"```(?:html)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
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
