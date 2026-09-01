import json
from pathlib import Path

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import EvaluationResult, FeatureModel
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient


class LLMEvaluator:
    """Evaluate generated pages using structured LLM and browser evidence."""

    def __init__(self, llm_client: LLMClient, model: str) -> None:
        self._llm_client = llm_client
        self._model = model
        self._system_prompt = Path("prompts/evaluator/llm.md").read_text(encoding="utf-8")

    def evaluate(
        self,
        features: FeatureModel,
        design_spec: DesignSpec,
        html: str,
        browser_result: BrowserEvaluationResult | None = None,
    ) -> EvaluationResult:
        if not self._model:
            raise ValueError("Evaluator model is not configured")

        payload = {
            "feature_model": features.model_dump(mode="json"),
            "design_spec": design_spec.model_dump(mode="json"),
            "generated_html": html,
            "browser_evaluation": (
                browser_result.model_dump(mode="json") if browser_result is not None else None
            ),
        }
        screenshot_paths = []
        if browser_result is not None and browser_result.enabled:
            screenshot_paths = [
                result.screenshot_path
                for result in browser_result.viewports
                if result.screenshot_path
            ]

        prompt = (
            f"{self._system_prompt}\n\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        response = self._llm_client.generate(
            prompt=prompt,
            model=self._model,
            options={"format": "json"},
            images=screenshot_paths or None,
        )
        return EvaluationResult.model_validate_json(response)
