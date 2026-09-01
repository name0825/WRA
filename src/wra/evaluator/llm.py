import json
import logging
from pathlib import Path

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import EvaluationResult, FeatureModel
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient
from wra.llm.response import normalize_json_response

logger = logging.getLogger(__name__)


class LLMEvaluator:
    """Evaluate generated pages using structured LLM and browser evidence."""

    def __init__(self, llm_client: LLMClient, model: str, think: bool | None = None, timeout: float | None = None) -> None:
        self._llm_client = llm_client
        self._model = model
        self._think = think
        self._timeout = timeout
        self._system_prompt = Path("prompts/evaluator/llm.md").read_text(encoding="utf-8")

    def evaluate(self, features: FeatureModel, design_spec: DesignSpec, html: str, browser_result: BrowserEvaluationResult | None = None, model: str | None = None, think: bool | None = None, timeout: float | None = None) -> EvaluationResult:
        selected_model = model or self._model
        selected_think = self._think if think is None else think
        selected_timeout = self._timeout if timeout is None else timeout
        if not selected_model:
            logger.error("Evaluator model is not configured")
            raise ValueError("Evaluator model is not configured")
        logger.info("Evaluation started: model=%s html_chars=%d browser_enabled=%s think=%s timeout=%s", selected_model, len(html), bool(browser_result and browser_result.enabled), selected_think, selected_timeout)
        payload = {
            "feature_model": features.model_dump(mode="json"),
            "design_spec": design_spec.model_dump(mode="json"),
            "generated_html": html,
            "browser_evaluation": browser_result.model_dump(mode="json") if browser_result is not None else None,
        }
        screenshot_paths = []
        if browser_result is not None and browser_result.enabled:
            screenshot_paths = [result.screenshot_path for result in browser_result.viewports if result.screenshot_path]
        prompt = f"{self._system_prompt}\n\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}"
        try:
            response = self._llm_client.generate(prompt=prompt, model=selected_model, options={"format": "json"}, images=screenshot_paths or None, think=selected_think, timeout=selected_timeout)
            normalized_response = normalize_json_response(response)
            if normalized_response != response.strip():
                logger.debug("Normalized evaluator JSON response: raw_chars=%d normalized_chars=%d", len(response), len(normalized_response))
            result = EvaluationResult.model_validate_json(normalized_response)
        except Exception:
            logger.exception("Evaluation failed: model=%s", selected_model)
            raise
        logger.info("Evaluation completed: model=%s score=%.2f", selected_model, result.overall_score)
        logger.debug("Evaluation details: %s", result.model_dump_json())
        return result
