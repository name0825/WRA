import json
from pathlib import Path
from typing import Any

from wra.core.models import FeatureModel
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient


class DesignPlanner:
    """Create a structured design plan from a generic web FeatureModel."""

    def __init__(
        self,
        llm_client: LLMClient,
        designer_model: str,
        prompt_path: str | Path = "prompts/designer/system.md",
    ) -> None:
        self._llm_client = llm_client
        self._designer_model = designer_model
        self._prompt_path = Path(prompt_path)

    def plan(
        self,
        features: FeatureModel,
        design_constraints: dict[str, Any] | None = None,
        generation_rules: list[str] | None = None,
    ) -> DesignSpec:
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        prompt = (
            f"{prompt_template}\n\n"
            "Feature Model:\n"
            f"{features.model_dump_json(indent=2)}\n\n"
            "Design Constraints:\n"
            f"{json.dumps(design_constraints or {}, ensure_ascii=False, indent=2)}\n\n"
            "Generation Rules:\n"
            f"{json.dumps(generation_rules or [], ensure_ascii=False, indent=2)}"
        )

        response = self._llm_client.generate(
            prompt=prompt,
            model=self._designer_model,
            options={"format": "json"},
        )
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: str) -> DesignSpec:
        try:
            data = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError("Designer LLM returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise ValueError("Designer LLM response must be a JSON object")

        try:
            return DesignSpec.model_validate(data)
        except Exception as exc:
            raise ValueError("Designer LLM response does not match DesignSpec") from exc
