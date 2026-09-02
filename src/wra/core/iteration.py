from dataclasses import dataclass
from pathlib import Path
import logging
import shutil

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import FACTUAL_ACCURACY_RULE, SOURCE_LANGUAGE_RULE, EvaluationResult, FeatureModel, WRAConfig, content_freedom_policy, coverage_is_enforced
from wra.core.output import OutputManager
from wra.designer.planner import DesignPlanner
from wra.designer.schema import DesignSpec
from wra.evaluator.llm import LLMEvaluator
from wra.generator.generator import HTMLGenerator
from wra.validator.html import HTMLValidationResult, validate_html
from wra.validator.tokens import find_corrupted_tokens
from wra.analyzer.analyzer import HTMLAnalyzer
from wra.llm.model_selector import ModelSelector

logger = logging.getLogger(__name__)


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

    def __init__(self, config, analyzer, designer, generator, evaluator, browser_evaluator=None):
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
        logger.info("Pipeline started: input=%s run_dir=%s", input_file, output_dir)
        original_path = output_dir / "original.html"
        if not original_path.exists():
            original_path.write_text(input_file.read_text(encoding="utf-8"), encoding="utf-8")
        features = self._analyzer.analyze_file(input_file)
        features_path = output_dir / "features.json"
        if not features_path.exists():
            features_path.write_text(features.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Feature Model ready: path=%s", features_path)
        output_manager = OutputManager(output_dir, self._config.pipeline.output.save_mode)
        return self._run_iterations(features, output_manager, iteration)

    def _run_iterations(self, features, output_manager, start_iteration):
        iteration = start_iteration
        last_result = None
        previous_result = None
        browser_tmp = output_manager.run_dir / ".browser_tmp"
        try:
            while True:
                logger.info("Iteration %d started", iteration)
                designer_model = ModelSelector.select(self._config.models.designer, "designer")
                generator_model = ModelSelector.select(self._config.models.generator, "generator")
                evaluator_model = ModelSelector.select(self._config.evaluation.model, "evaluator")
                logger.info("Models selected: designer=%s think=%s timeout=%s generator=%s think=%s timeout=%s evaluator=%s think=%s timeout=%s", designer_model.name, designer_model.think, designer_model.timeout, generator_model.name, generator_model.think, generator_model.timeout, evaluator_model.name, evaluator_model.think, evaluator_model.timeout)

                design_constraints = self._build_design_constraints(iteration)
                logger.debug("Design constraints: %s", design_constraints)
                if previous_result is None or previous_result.evaluation is None:
                    logger.info("Current task: design planning")
                    design_spec = self._designer.plan(features, design_constraints=design_constraints, model=designer_model.name, think=designer_model.think, timeout=designer_model.timeout)
                else:
                    logger.info("Current task: design revision based on score=%.2f", previous_result.evaluation.overall_score)
                    design_spec = self._designer.revise(features=features, previous_design_spec=previous_result.design_spec, previous_html=previous_result.html, evaluation=previous_result.evaluation, design_constraints=design_constraints, model=designer_model.name, think=designer_model.think, timeout=designer_model.timeout)

                logger.info("Current task: HTML generation")
                html = self._generator.generate(features, design_spec, generation_rules=self._build_generation_rules(), model=generator_model.name, think=generator_model.think, timeout=generator_model.timeout)
                logger.info("Current task: HTML validation")
                validation = validate_html(html, features)
                browser_result = BrowserEvaluationResult(enabled=False)
                evaluation = None
                if validation.valid:
                    if self._browser_evaluator is not None and self._config.browser.enabled:
                        logger.info("Current task: browser evaluation")
                        browser_result = self._browser_evaluator.evaluate(html=html, output_dir=browser_tmp, iteration=iteration)
                    logger.info("Current task: LLM evaluation")
                    evaluation = self._evaluator.evaluate(features, design_spec, html, browser_result=browser_result, content_findings=find_corrupted_tokens(html, features), model=evaluator_model.name, think=evaluator_model.think, timeout=evaluator_model.timeout)
                else:
                    logger.warning("Skipping browser/evaluation because HTML validation failed")

                logger.info("Current task: saving iteration %d", iteration)
                saved = output_manager.save_iteration(iteration=iteration, html=html, design_spec=design_spec, validation=validation, evaluation=evaluation, browser=browser_result, score=evaluation.overall_score if evaluation else None)
                last_result = IterationResult(iteration=iteration, features=features, design_spec=design_spec, html=html, validation=validation, browser=browser_result, evaluation=evaluation, html_path=saved.html_path, validation_path=saved.validation_path, final_path=output_manager.run_dir / "final.html")
                logger.info("Iteration %d completed: validation=%s score=%s", iteration, validation.valid, f"{evaluation.overall_score:.2f}" if evaluation else "n/a")
                if self._should_terminate(iteration, evaluation):
                    logger.info("Pipeline termination condition met at iteration %d", iteration)
                    return self._finalize(last_result, output_manager, browser_tmp)
                previous_result = last_result
                iteration += 1
        except KeyboardInterrupt:
            logger.warning("Pipeline interrupted by user")
            if last_result is None:
                raise
            return self._finalize(last_result, output_manager, browser_tmp)
        except Exception:
            logger.exception("Pipeline failed")
            raise
        finally:
            if browser_tmp.exists():
                shutil.rmtree(browser_tmp, ignore_errors=True)

    def _build_design_constraints(self, iteration: int) -> dict:
        mode = self._config.design.mode
        constraints = {"mode": mode}
        if mode != "auto":
            constraints["available_presets"] = self._config.design.presets
        exploration_axes = (
            "Explore composition and information hierarchy; avoid defaulting to a conventional business landing-page structure.",
            "Explore typography, density, and spatial rhythm; allow a substantially different reading flow when content supports it.",
            "Explore color relationships, surfaces, contrast, and visual texture; do not assume a dark corporate palette.",
            "Explore interaction and motion as appropriate; consider different interaction models without sacrificing clarity or accessibility.",
        )
        constraints["variation_policy"] = {"type": "soft_guidance", "instruction": exploration_axes[(iteration - 1) % len(exploration_axes)], "do_not_force": True}
        freedom = self._config.content.freedom
        policy = content_freedom_policy(freedom)
        constraints["content_freedom"] = {"level": freedom, "instruction": policy["planning"], "coverage_required": coverage_is_enforced(freedom), "factual_accuracy": FACTUAL_ACCURACY_RULE, "source_language": SOURCE_LANGUAGE_RULE}
        return constraints

    def _build_generation_rules(self) -> list[str]:
        freedom = self._config.content.freedom
        return [
            f"Content freedom `{freedom}`: {content_freedom_policy(freedom)['authoring']}",
            f"Factual accuracy: {FACTUAL_ACCURACY_RULE}",
            f"Source language: {SOURCE_LANGUAGE_RULE}",
        ]

    def _finalize(self, result, output_manager, browser_tmp):
        logger.info("Finalizing output from iteration %d", result.iteration)
        final_path = output_manager.finalize(fallback_html=result.html)
        logger.info("Final output ready: %s", final_path)
        return IterationResult(iteration=result.iteration, features=result.features, design_spec=result.design_spec, html=result.html, validation=result.validation, browser=result.browser, evaluation=result.evaluation, html_path=result.html_path, validation_path=result.validation_path, final_path=final_path)

    def _should_terminate(self, iteration, evaluation):
        termination = self._config.pipeline.termination
        if not termination.enabled:
            logger.info("Termination disabled: continuing to next iteration")
            return False
        if evaluation is not None and evaluation.overall_score >= self._config.pipeline.evaluation_threshold:
            logger.info("Evaluation threshold reached: %.2f >= %.2f", evaluation.overall_score, self._config.pipeline.evaluation_threshold)
            return True
        if iteration >= self._config.pipeline.max_iterations:
            logger.info("Maximum iterations reached: %d", iteration)
            return True
        return False
