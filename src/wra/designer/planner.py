import json
from pathlib import Path
from typing import Any

from wra.core.models import EvaluationResult, FeatureModel
from wra.designer.recovery import (
    DesignRecoveryError,
    RecoveryPolicy,
    RecoveryStrategy,
    build_recovery_prompt,
    classify_exception,
    strategy_for,
)
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient
from wra.validator.design import validate_design_spec


class DesignPlanner:
    """Create and revise structured design plans from a generic web FeatureModel."""

    def __init__(
        self,
        llm_client: LLMClient,
        designer_model: str,
        prompt_path: str | Path = "prompts/designer/system.md",
        revision_prompt_path: str | Path = "prompts/designer/revision.md",
        recovery_policy: RecoveryPolicy | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._designer_model = designer_model
        self._prompt_path = Path(prompt_path)
        self._revision_prompt_path = Path(revision_prompt_path)
        self._recovery_policy = recovery_policy or RecoveryPolicy()

    def plan(
        self,
        features: FeatureModel,
        design_constraints: dict[str, Any] | None = None,
        generation_rules: list[str] | None = None,
    ) -> DesignSpec:
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        original_prompt = self._build_plan_prompt(
            prompt_template,
            features,
            design_constraints,
            generation_rules,
        )
        return self._generate_and_validate(
            original_prompt=original_prompt,
            features=features,
            revision_template=self._revision_prompt_path.read_text(encoding="utf-8"),
        )

    def revise(
        self,
        features: FeatureModel,
        previous_design_spec: DesignSpec,
        previous_html: str,
        evaluation: EvaluationResult,
    ) -> DesignSpec:
        """Revise the previous DesignSpec from evaluator feedback instead of redesigning from scratch."""
        revision_template = self._revision_prompt_path.read_text(encoding="utf-8")
        prompt = revision_template.format(
            previous_design_spec=previous_design_spec.model_dump_json(indent=2),
            previous_html=previous_html,
            evaluation_result=evaluation.model_dump_json(indent=2),
        )
        return self._generate_and_validate(
            original_prompt=prompt,
            features=features,
            revision_template=revision_template,
        )

    def _generate_and_validate(
        self,
        original_prompt: str,
        features: FeatureModel,
        revision_template: str,
    ) -> DesignSpec:
        current_num_ctx = self._recovery_policy.initial_num_ctx
        previous_response: str | None = None
        prompt = original_prompt
        last_error = DesignRecoveryError.UNKNOWN
        last_details: list[str] = []

        for retry in range(self._recovery_policy.max_retries + 1):
            try:
                response = self._llm_client.generate(
                    prompt=prompt,
                    model=self._designer_model,
                    options={"format": "json", "num_ctx": current_num_ctx},
                )
                previous_response = response
                spec = self._parse_response(response)
                validation = validate_design_spec(spec, features)
                if not validation.valid:
                    last_error = DesignRecoveryError.INVALID_DESIGN
                    last_details = validation.errors
                    strategy = strategy_for(last_error)
                else:
                    return spec
            except ValueError as exc:
                last_error = self._classify_parse_error(exc)
                last_details = [str(exc)]
                strategy = strategy_for(last_error)
            except Exception as exc:
                last_error = classify_exception(exc)
                last_details = [str(exc)]
                strategy = strategy_for(last_error)

            if retry >= self._recovery_policy.max_retries:
                break

            if strategy is RecoveryStrategy.INCREASE_CONTEXT:
                next_num_ctx = self._recovery_policy.next_num_ctx(current_num_ctx)
                if next_num_ctx != current_num_ctx:
                    current_num_ctx = next_num_ctx
                    prompt = build_recovery_prompt(
                        original_prompt,
                        previous_response,
                        last_error,
                        last_details,
                        revision_template,
                    )
                    continue
                strategy = RecoveryStrategy.REGENERATE

            if strategy is RecoveryStrategy.REVISE_RESPONSE:
                prompt = build_recovery_prompt(
                    original_prompt,
                    previous_response,
                    last_error,
                    last_details,
                    revision_template,
                )
            else:
                prompt = original_prompt
                previous_response = None

        raise RuntimeError(
            f"Design planning failed after {self._recovery_policy.max_retries} retries: "
            f"{last_error.value}; {', '.join(last_details)}"
        )

    @staticmethod
    def _build_plan_prompt(
        prompt_template: str,
        features: FeatureModel,
        design_constraints: dict[str, Any] | None,
        generation_rules: list[str] | None,
    ) -> str:
        return (
            f"{prompt_template}\n\n"
            "Feature Model:\n"
            f"{features.model_dump_json(indent=2)}\n\n"
            "Design Constraints:\n"
            f"{json.dumps(design_constraints or {}, ensure_ascii=False, indent=2)}\n\n"
            "Generation Rules:\n"
            f"{json.dumps(generation_rules or [], ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _classify_parse_error(exc: ValueError) -> DesignRecoveryError:
        message = str(exc).lower()
        if "invalid json" in message:
            return DesignRecoveryError.JSON_STRUCTURE
        if "does not match designspec" in message:
            return DesignRecoveryError.INVALID_CONTENT
        return DesignRecoveryError.UNKNOWN

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
