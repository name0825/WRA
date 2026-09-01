import json
from pathlib import Path

from wra.core.models import EvaluationResult, FeatureModel
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient


class LLMEvaluator:
    """Evaluate a generated page against the original features and design specification."""

    def __init__(self, llm_client: LLMClient, model: str) -> None:
        self._llm_client = llm_client
        self._model = model
        self._system_prompt = Path("prompts/evaluator/llm.md").read_text(encoding="utf-8")

    def evaluate(
        self,
        features: FeatureModel,
        design_spec: DesignSpec,
        html: str,
    ) -> EvaluationResult:
        if not self._model:
            raise ValueError("Evaluator model is not configured")

        payload = {
            "feature_model": features.model_dump(mode="json"),
            "design_spec": design_spec.model_dump(mode="json"),
            "generated_html": html,
        }
        prompt = (
            f"{self._system_prompt}\n\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        response = self._llm_client.generate(
            prompt=prompt,
            model=self._model,
            options={"format": "json"},
        )
        return EvaluationResult.model_validate_json(response)
