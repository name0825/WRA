from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.core.iteration import IterationEngine
from wra.core.models import WRAConfig
from wra.designer.planner import DesignPlanner
from wra.designer.schema import DesignSpec
from wra.evaluator.llm import LLMEvaluator
from wra.generator.generator import HTMLGenerator
from wra.validator.html import HTMLValidationResult


@dataclass(frozen=True)
class PipelineResult:
    features_path: Path
    design_spec_path: Path
    html_path: Path
    validation_path: Path
    features: Any
    design_spec: DesignSpec
    html: str
    validation: HTMLValidationResult
    evaluation: Any | None
    final_path: Path


class WRAPipeline:
    """Run the complete Analyze -> Design -> Generate -> Validate -> Evaluate loop."""

    def __init__(
        self,
        config: WRAConfig,
        analyzer: HTMLAnalyzer,
        designer: DesignPlanner,
        generator: HTMLGenerator,
        evaluator: LLMEvaluator,
    ) -> None:
        self._engine = IterationEngine(
            config=config,
            analyzer=analyzer,
            designer=designer,
            generator=generator,
            evaluator=evaluator,
        )

    def run(self, input_path: str | Path, run_dir: str | Path) -> PipelineResult:
        result = self._engine.run(input_path, run_dir)
        output_dir = Path(run_dir)
        features_path = output_dir / "features.json"

        if result.html_path is None:
            html_path = output_dir / "final.html"
        else:
            html_path = result.html_path

        if result.validation_path is None:
            validation_path = output_dir / "result.json"
        else:
            validation_path = result.validation_path

        features = self._engine._analyzer.analyze_file(input_path)
        return PipelineResult(
            features_path=features_path,
            design_spec_path=(
                result.html_path.parent / "design.json"
                if result.html_path is not None
                else output_dir / "final.html"
            ),
            html_path=html_path,
            validation_path=validation_path,
            features=features,
            design_spec=result.design_spec,
            html=result.html,
            validation=result.validation,
            evaluation=result.evaluation,
            final_path=result.final_path,
        )
