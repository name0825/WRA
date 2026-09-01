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
        iteration_dir = result.html_path.parent if result.html_path is not None else output_dir

        return PipelineResult(
            features_path=output_dir / "features.json",
            design_spec_path=iteration_dir / "design.json",
            html_path=result.html_path or result.final_path,
            validation_path=result.validation_path or output_dir / "result.json",
            features=result.features,
            design_spec=result.design_spec,
            html=result.html,
            validation=result.validation,
            evaluation=result.evaluation,
            final_path=result.final_path,
        )
