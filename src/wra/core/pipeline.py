from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.core.models import WRAConfig
from wra.designer.planner import DesignPlanner
from wra.designer.schema import DesignSpec
from wra.generator.generator import HTMLGenerator
from wra.validator.html import HTMLValidationResult, validate_html


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


class WRAPipeline:
    """Run the currently implemented Analyze -> Design -> Generate -> Validate pipeline."""

    def __init__(
        self,
        config: WRAConfig,
        analyzer: HTMLAnalyzer,
        designer: DesignPlanner,
        generator: HTMLGenerator,
    ) -> None:
        self._config = config
        self._analyzer = analyzer
        self._designer = designer
        self._generator = generator

    def run(self, input_path: str | Path, run_dir: str | Path) -> PipelineResult:
        input_file = Path(input_path)
        output_dir = Path(run_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        original_path = output_dir / "original.html"
        original_path.write_text(input_file.read_text(encoding="utf-8"), encoding="utf-8")

        features = self._analyzer.analyze_file(input_file)
        features_path = output_dir / "features.json"
        features_path.write_text(
            features.model_dump_json(indent=2),
            encoding="utf-8",
        )

        design_spec = self._designer.plan(features)
        design_dir = output_dir / "design"
        design_dir.mkdir(exist_ok=True)
        design_spec_path = design_dir / "iteration_1.json"
        design_spec_path.write_text(
            design_spec.model_dump_json(indent=2),
            encoding="utf-8",
        )

        html = self._generator.generate(features, design_spec)
        html_dir = output_dir / "html"
        html_dir.mkdir(exist_ok=True)
        html_path = html_dir / "iteration_1.html"
        html_path.write_text(html, encoding="utf-8")

        validation = validate_html(html, features)
        validation_dir = output_dir / "validation"
        validation_dir.mkdir(exist_ok=True)
        validation_path = validation_dir / "iteration_1.json"
        validation_path.write_text(
            json.dumps(asdict(validation), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        final_path = output_dir / "final.html"
        final_path.write_text(html, encoding="utf-8")

        return PipelineResult(
            features_path=features_path,
            design_spec_path=design_spec_path,
            html_path=html_path,
            validation_path=validation_path,
            features=features,
            design_spec=design_spec,
            html=html,
            validation=validation,
        )
