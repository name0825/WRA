import json
import logging
import re
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

logger = logging.getLogger(__name__)


class DesignPlanner:
    """Create and revise structured design plans from a generic web FeatureModel."""

    def __init__(self, llm_client: LLMClient, designer_model: str, prompt_path: str | Path = "prompts/designer/system.md", revision_prompt_path: str | Path = "prompts/designer/revision.md", recovery_policy: RecoveryPolicy | None = None, designer_think: bool | None = None, designer_timeout: float | None = None) -> None:
        self._llm_client = llm_client
        self._designer_model = designer_model
        self._designer_think = designer_think
        self._designer_timeout = designer_timeout
        self._prompt_path = Path(prompt_path)
        self._revision_prompt_path = Path(revision_prompt_path)
        self._recovery_policy = recovery_policy or RecoveryPolicy()

    def plan(self, features: FeatureModel, design_constraints: dict[str, Any] | None = None, generation_rules: list[str] | None = None, model: str | None = None, think: bool | None = None, timeout: float | None = None) -> DesignSpec:
        selected_model = model or self._designer_model
        selected_think = self._designer_think if think is None else think
        selected_timeout = self._designer_timeout if timeout is None else timeout
        logger.info("Design planning started: model=%s mode=%s", selected_model, (design_constraints or {}).get("mode", "unspecified"))
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        original_prompt = self._build_plan_prompt(prompt_template, features, design_constraints, generation_rules)
        return self._generate_and_validate(original_prompt=original_prompt, features=features, revision_template=self._revision_prompt_path.read_text(encoding="utf-8"), model=selected_model, think=selected_think, timeout=selected_timeout)

    def revise(self, features: FeatureModel, previous_design_spec: DesignSpec, previous_html: str, evaluation: EvaluationResult, design_constraints: dict[str, Any] | None = None, model: str | None = None, think: bool | None = None, timeout: float | None = None) -> DesignSpec:
        selected_model = model or self._designer_model
        selected_think = self._designer_think if think is None else think
        selected_timeout = self._designer_timeout if timeout is None else timeout
        logger.info("Design revision started: model=%s previous_score=%.2f", selected_model, evaluation.overall_score)
        revision_template = self._revision_prompt_path.read_text(encoding="utf-8")
        prompt = revision_template.format(previous_design_spec=previous_design_spec.model_dump_json(indent=2), previous_html=previous_html, evaluation_result=evaluation.model_dump_json(indent=2))
        if design_constraints:
            prompt = self._append_design_constraints(prompt, design_constraints)
        return self._generate_and_validate(original_prompt=prompt, features=features, revision_template=revision_template, model=selected_model, think=selected_think, timeout=selected_timeout)

    def _generate_and_validate(self, original_prompt, features, revision_template, model, think=None, timeout=None):
        current_num_ctx = self._recovery_policy.initial_num_ctx
        previous_response = None
        prompt = original_prompt
        last_error = DesignRecoveryError.UNKNOWN
        last_details: list[str] = []

        for retry in range(self._recovery_policy.max_retries + 1):
            attempt = retry + 1
            logger.info("Design Planner attempt %d/%d: model=%s num_ctx=%d think=%s timeout=%s", attempt, self._recovery_policy.max_retries + 1, model, current_num_ctx, think, timeout)
            try:
                response = self._llm_client.generate(prompt=prompt, model=model, options={"format": "json", "num_ctx": current_num_ctx}, think=think, timeout=timeout)
                previous_response = response
                logger.debug("Designer response received: chars=%d", len(response))
                spec = self._parse_response(response)
                logger.debug("DesignSpec JSON parsed successfully")
                validation = validate_design_spec(spec, features)
                if not validation.valid:
                    last_error = DesignRecoveryError.INVALID_DESIGN
                    last_details = validation.errors
                    logger.warning("DesignSpec validation failed: errors=%s", validation.errors)
                    strategy = strategy_for(last_error)
                else:
                    logger.info("DesignSpec validation passed: sections=%d", len(spec.sections))
                    return spec
            except ValueError as exc:
                last_error = self._classify_parse_error(exc)
                last_details = [str(exc)]
                logger.warning("Design Planner validation/parsing failure: type=%s detail=%s", last_error.value, exc)
                strategy = strategy_for(last_error)
            except Exception as exc:
                last_error = classify_exception(exc)
                last_details = [str(exc)]
                logger.exception("Design Planner attempt failed: type=%s", last_error.value)
                strategy = strategy_for(last_error)

            if retry >= self._recovery_policy.max_retries:
                break
            if strategy is RecoveryStrategy.INCREASE_CONTEXT:
                next_num_ctx = self._recovery_policy.next_num_ctx(current_num_ctx)
                if next_num_ctx != current_num_ctx:
                    logger.info("Design Planner recovery: increasing num_ctx %d -> %d", current_num_ctx, next_num_ctx)
                    current_num_ctx = next_num_ctx
                    prompt = build_recovery_prompt(original_prompt, previous_response, last_error, last_details, revision_template)
                    continue
                strategy = RecoveryStrategy.REGENERATE
            if strategy is RecoveryStrategy.REVISE_RESPONSE:
                logger.info("Design Planner recovery: reusing previous response")
                prompt = build_recovery_prompt(original_prompt, previous_response, last_error, last_details, revision_template)
            else:
                logger.info("Design Planner recovery: regenerating from original prompt")
                prompt = original_prompt
                previous_response = None

        logger.error("Design planning exhausted retries: reason=%s details=%s", last_error.value, last_details)
        raise RuntimeError(f"Design planning failed after {self._recovery_policy.max_retries} retries: {last_error.value}; {', '.join(last_details)}")

    @staticmethod
    def _build_plan_prompt(prompt_template, features, design_constraints, generation_rules):
        return (
            f"{prompt_template}\n\n"
            f"Feature Model:\n{features.model_dump_json(indent=2)}\n\n"
            f"Available Feature Model reference paths:\n{DesignPlanner._feature_reference_paths(features)}\n\n"
            f"Design Constraints:\n{json.dumps(design_constraints or {}, ensure_ascii=False, indent=2)}\n\n"
            f"Generation Rules:\n{json.dumps(generation_rules or [], ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _feature_reference_paths(features: FeatureModel) -> str:
        paths: list[str] = []

        def visit(value: Any, prefix: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    path = f"{prefix}.{key}" if prefix else key
                    if child is not None:
                        paths.append(path)
                        if isinstance(child, dict):
                            visit(child, path)
            elif isinstance(value, list):
                return

        visit(features.model_dump(exclude_none=True), "")
        return "\n".join(f"- `{path}`" for path in paths)

    @staticmethod
    def _append_design_constraints(prompt: str, design_constraints: dict[str, Any]) -> str:
        return f"{prompt}\n\nDesign Constraints:\n{json.dumps(design_constraints, ensure_ascii=False, indent=2)}"

    @staticmethod
    def _classify_parse_error(exc: ValueError) -> DesignRecoveryError:
        message = str(exc).lower()
        if any(marker in message for marker in ("truncated", "unexpected end", "unterminated json", "output token limit")):
            return DesignRecoveryError.TOKEN_LIMIT
        if "invalid json" in message:
            return DesignRecoveryError.JSON_STRUCTURE
        if "does not match designspec" in message:
            return DesignRecoveryError.INVALID_CONTENT
        return DesignRecoveryError.UNKNOWN

    @staticmethod
    def _looks_like_truncated_json(candidate: str, error: json.JSONDecodeError) -> bool:
        """Detect JSON that likely stopped because the model exhausted its output budget."""
        text = candidate.rstrip()
        if len(text) < 256 or not text.startswith("{"):
            return False

        tail_window = max(64, len(text) // 50)
        error_near_end = error.pos >= max(0, len(text) - tail_window)
        message = error.msg.lower()
        unterminated = "unterminated string" in message
        unclosed = message.startswith("expecting") and error.pos >= max(0, len(text) - 32)
        return error_near_end or unterminated or unclosed

    @staticmethod
    def _parse_response(response: str) -> DesignSpec:
        text = response.lstrip("\ufeff").strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        text = re.sub(r"<think>.*$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()

        candidates: list[str] = [text]
        candidates.extend(match.strip() for match in re.findall(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL))

        decoder = json.JSONDecoder()
        truncated_detected = False
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError as exc:
                if DesignPlanner._looks_like_truncated_json(candidate, exc):
                    truncated_detected = True
                data = None

            if isinstance(data, dict):
                try:
                    return DesignSpec.model_validate(data)
                except Exception as exc:
                    raise ValueError("Designer LLM response does not match DesignSpec") from exc

            # Decode only from the first opening brace. Scanning every `{` can
            # accidentally accept a nested object from a truncated root object,
            # causing TOKEN_LIMIT to be misclassified as INVALID_CONTENT.
            start = candidate.find("{")
            if start < 0:
                continue
            fragment = candidate[start:]
            try:
                data, _ = decoder.raw_decode(fragment)
            except json.JSONDecodeError as exc:
                if DesignPlanner._looks_like_truncated_json(fragment, exc):
                    truncated_detected = True
                continue

            if not isinstance(data, dict):
                continue
            try:
                return DesignSpec.model_validate(data)
            except Exception:
                continue

        if truncated_detected:
            raise ValueError("Designer LLM response appears truncated, likely due to output token limit")
        raise ValueError("Designer LLM returned invalid JSON")
