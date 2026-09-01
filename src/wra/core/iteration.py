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
    design_spec: DesignSpec
    html: str
    validation: HTMLValidationResult
    evaluation: EvaluationResult | None
    html_path: Path | None
    validation_path: Path | None
    final_path: Path


class IterationEngine:
    """Execute generation and LLM-based evaluation for an iteration."""

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

        features: FeatureModel = self._analyzer.analyze_file(input_file)
        features_path = output_dir / "features.json"
        if not features_path.exists():
            features_path.write_text(features.model_dump_json(indent=2), encoding="utf-8")

        design_spec = self._designer.plan(features)
        html = self._generator.generate(features, design_spec)
        validation = validate_html(html, features)
        evaluation = self._evaluator.evaluate(features, design_spec, html) if validation.valid else None

        output_manager = OutputManager(output_dir, self._config.pipeline.output.save_mode)
        saved = output_manager.save_iteration(
            iteration=iteration,
            html=html,
            design_spec=design_spec,
            validation=validation,
            evaluation=evaluation,
        )
        final_path = output_manager.finalize(fallback_html=html)

        return IterationResult(
            iteration=iteration,
            design_spec=design_spec,
            html=html,
            validation=validation,
            evaluation=evaluation,
            html_path=saved.html_path,
            validation_path=saved.validation_path,
            final_path=final_path,
        )
