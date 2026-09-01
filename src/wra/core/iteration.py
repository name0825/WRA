from dataclasses import dataclass
from pathlib import Path

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.core.models import EvaluationResult, FeatureModel, WRAConfig
from wra.core.output import OutputManager
from wra.designer.planner import DesignPlanner
from wra.designer.schema import DesignSpec
from wra.evaluator.llm import LLMEvaluator
from wra.generator.generator import HTMLGenerator
from wra.validator.html import HTMLValidationResult, validate_html


@dataclass(frozen=True)
class IterationResult:
    iteration: int
    features: FeatureModel
    design_spec: DesignSpec
    html: str
    validation: HTMLValidationResult
    evaluation: EvaluationResult | None
    html_path: Path | None
    validation_path: Path | None
    final_path: Path


class IterationEngine:
    """Execute generation, validation, evaluation, and iteration control."""

    def __init__(
        self,
        config: WRAConfig,
        analyzer: HTMLAnalyzer,
        designer: DesignPlanner,
        generator: HTMLGenerator,
        evaluator: LLMEvaluator,
    ) -> None:
        self._config = config
        self._analyzer = analyzer
        self._designer = designer
        self._generator = generator
        self._evaluator = evaluator

    def run(
        self,
        input_path: str | Path,
        run_dir: str | Path,
        iteration: int = 1,
    ) -> IterationResult:
        input_file = Path(input_path)
        output_dir = Path(run_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        original_path = output_dir / "original.html"
        if not original_path.exists():
            original_path.write_text(input_file.read_text(encoding="utf-8"), encoding="utf-8")

        features: FeatureModel = self._analyzer.analyze_file(input_file)
        features_path = output_dir / "features.json"
        if not features_path.exists():
            features_path.write_text(features.model_dump_json(indent=2), encoding="utf-8")

        output_manager = OutputManager(output_dir, self._config.pipeline.output.save_mode)
        return self._run_iterations(features, output_manager, iteration)

    def _run_iterations(
        self,
        features: FeatureModel,
        output_manager: OutputManager,
        start_iteration: int,
    ) -> IterationResult:
        iteration = start_iteration
        last_result: IterationResult | None = None
        previous_evaluation: EvaluationResult | None = None

        try:
            while True:
                design_spec = self._designer.plan(
                    features,
                    generation_rules=self._revision_rules(previous_evaluation),
                )
                html = self._generator.generate(features, design_spec)
                validation = validate_html(html, features)
                evaluation = (
                    self._evaluator.evaluate(features, design_spec, html)
                    if validation.valid
                    else None
                )

                saved = output_manager.save_iteration(
                    iteration=iteration,
                    html=html,
                    design_spec=design_spec,
                    validation=validation,
                    evaluation=evaluation,
                    score=evaluation.overall_score if evaluation else None,
                )

                last_result = IterationResult(
                    iteration=iteration,
                    features=features,
                    design_spec=design_spec,
                    html=html,
                    validation=validation,
                    evaluation=evaluation,
                    html_path=saved.html_path,
                    validation_path=saved.validation_path,
                    final_path=output_dir / "final.html",
                )

                if self._should_terminate(iteration, evaluation):
                    return self._finalize(last_result, output_manager)

                previous_evaluation = evaluation
                iteration += 1
        except KeyboardInterrupt:
            if last_result is None:
                raise
            return self._finalize(last_result, output_manager)

    def _finalize(self, result: IterationResult, output_manager: OutputManager) -> IterationResult:
        final_path = output_manager.finalize(fallback_html=result.html)
        return IterationResult(
            iteration=result.iteration,
            features=result.features,
            design_spec=result.design_spec,
            html=result.html,
            validation=result.validation,
            evaluation=result.evaluation,
            html_path=result.html_path,
            validation_path=result.validation_path,
            final_path=final_path,
        )

    def _should_terminate(
        self,
        iteration: int,
        evaluation: EvaluationResult | None,
    ) -> bool:
        termination = self._config.pipeline.termination
        if not termination.enabled:
            return False

        if evaluation is not None and evaluation.overall_score >= self._config.pipeline.evaluation_threshold:
            return True

        return iteration >= self._config.pipeline.max_iterations

    @staticmethod
    def _revision_rules(evaluation: EvaluationResult | None) -> list[str]:
        if evaluation is None:
            return []

        rules = [
            f"Previous overall evaluation score: {evaluation.overall_score:.2f}/10.",
        ]
        if evaluation.summary:
            rules.append(f"Previous evaluation summary: {evaluation.summary}")
        rules.extend(
            f"Improve according to evaluator feedback: {suggestion}"
            for suggestion in evaluation.improvement_suggestions
        )
        return rules
