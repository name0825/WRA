from dataclasses import dataclass
from pathlib import Path
import shutil

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import EvaluationResult, FeatureModel, WRAConfig
from wra.core.output import OutputManager
from wra.designer.planner import DesignPlanner
from wra.designer.schema import DesignSpec
from wra.evaluator.llm import LLMEvaluator
from wra.generator.generator import HTMLGenerator
from wra.validator.html import HTMLValidationResult, validate_html
from wra.analyzer.analyzer import HTMLAnalyzer


@dataclass(frozen=True)
class IterationResult:
    iteration: int
    features: FeatureModel
    design_spec: DesignSpec
    html: str
    validation: HTMLValidationResult
    browser: BrowserEvaluationResult
    evaluation: EvaluationResult | None
    html_path: Path | None
    validation_path: Path | None
    final_path: Path


class IterationEngine:
    """Execute generation, validation, browser evaluation, scoring, and revision."""

    def __init__(
        self,
        config: WRAConfig,
        analyzer: HTMLAnalyzer,
        designer: DesignPlanner,
        generator: HTMLGenerator,
        evaluator: LLMEvaluator,
        browser_evaluator=None,
    ) -> None:
        self._config = config
        self._analyzer = analyzer
        self._designer = designer
        self._generator = generator
        self._evaluator = evaluator
        self._browser_evaluator = browser_evaluator

    def run(self, input_path: str | Path, run_dir: str | Path, iteration: int = 1) -> IterationResult:
        input_file = Path(input_path)
        output_dir = Path(run_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        original_path = output_dir / "original.html"
        if not original_path.exists():
            original_path.write_text(input_file.read_text(encoding="utf-8"), encoding="utf-8")

        features = self._analyzer.analyze_file(input_file)
        features_path = output_dir / "features.json"
        if not features_path.exists():
            features_path.write_text(features.model_dump_json(indent=2), encoding="utf-8")

        output_manager = OutputManager(output_dir, self._config.pipeline.output.save_mode)
        return self._run_iterations(features, output_manager, iteration)

    def _run_iterations(self, features, output_manager, start_iteration):
        iteration = start_iteration
        last_result = None
        previous_result = None
        browser_tmp = output_manager.run_dir / ".browser_tmp"

        try:
            while True:
                if previous_result is None or previous_result.evaluation is None:
                    design_spec = self._designer.plan(features)
                else:
                    design_spec = self._designer.revise(
                        features=features,
                        previous_design_spec=previous_result.design_spec,
                        previous_html=previous_result.html,
                        evaluation=previous_result.evaluation,
                    )

                html = self._generator.generate(features, design_spec)
                validation = validate_html(html, features)
                browser_result = BrowserEvaluationResult(enabled=False)
                evaluation = None

                if validation.valid:
                    if self._browser_evaluator is not None and self._config.browser.enabled:
                        browser_result = self._browser_evaluator.evaluate(
                            html=html,
                            output_dir=browser_tmp,
                            iteration=iteration,
                        )
                    evaluation = self._evaluator.evaluate(
                        features,
                        design_spec,
                        html,
                        browser_result=browser_result,
                    )

                saved = output_manager.save_iteration(
                    iteration=iteration,
                    html=html,
                    design_spec=design_spec,
                    validation=validation,
                    evaluation=evaluation,
                    browser=browser_result,
                    score=evaluation.overall_score if evaluation else None,
                )

                last_result = IterationResult(
                    iteration=iteration,
                    features=features,
                    design_spec=design_spec,
                    html=html,
                    validation=validation,
                    browser=browser_result,
                    evaluation=evaluation,
                    html_path=saved.html_path,
                    validation_path=saved.validation_path,
                    final_path=output_manager.run_dir / "final.html",
                )

                if self._should_terminate(iteration, evaluation):
                    return self._finalize(last_result, output_manager, browser_tmp)

                previous_result = last_result
                iteration += 1
        except KeyboardInterrupt:
            if last_result is None:
                raise
            return self._finalize(last_result, output_manager, browser_tmp)
        finally:
            if browser_tmp.exists():
                shutil.rmtree(browser_tmp, ignore_errors=True)

    def _finalize(self, result, output_manager, browser_tmp):
        final_path = output_manager.finalize(fallback_html=result.html)
        return IterationResult(
            iteration=result.iteration,
            features=result.features,
            design_spec=result.design_spec,
            html=result.html,
            validation=result.validation,
            browser=result.browser,
            evaluation=result.evaluation,
            html_path=result.html_path,
            validation_path=result.validation_path,
            final_path=final_path,
        )

    def _should_terminate(self, iteration, evaluation):
        termination = self._config.pipeline.termination
        if not termination.enabled:
            return False
        if evaluation is not None and evaluation.overall_score >= self._config.pipeline.evaluation_threshold:
            return True
        return iteration >= self._config.pipeline.max_iterations
