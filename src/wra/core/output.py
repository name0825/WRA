from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IterationOutput:
    iteration: int
    html_path: Path | None
    design_spec_path: Path | None
    validation_path: Path | None
    evaluation_path: Path | None


class OutputManager:
    """Persist iteration artifacts according to the configured save mode."""

    def __init__(self, run_dir: str | Path, save_mode: str) -> None:
        if save_mode not in {"all", "best"}:
            raise ValueError("output.save_mode must be 'all' or 'best'")

        self.run_dir = Path(run_dir)
        self.save_mode = save_mode
        self.iterations_dir = self.run_dir / "iterations"
        self._best_score: float | None = None
        self._best_html: str | None = None
        self._best_iteration: int | None = None

    def save_iteration(
        self,
        iteration: int,
        html: str,
        design_spec: Any | None = None,
        validation: Any | None = None,
        evaluation: Any | None = None,
        score: float | None = None,
    ) -> IterationOutput:
        """Save an iteration according to save_mode and track the best result."""
        html_path = design_spec_path = validation_path = evaluation_path = None

        if self.save_mode == "all":
            iteration_dir = self.iterations_dir / f"iteration_{iteration:03d}"
            iteration_dir.mkdir(parents=True, exist_ok=True)

            html_path = iteration_dir / "html.html"
            html_path.write_text(html, encoding="utf-8")

            if design_spec is not None:
                design_spec_path = iteration_dir / "design.json"
                self._write_json(design_spec_path, design_spec)

            if validation is not None:
                validation_path = iteration_dir / "validation.json"
                self._write_json(validation_path, validation)

            if evaluation is not None:
                evaluation_path = iteration_dir / "evaluation.json"
                self._write_json(evaluation_path, evaluation)

        if score is None or self._best_score is None or score > self._best_score:
            self._best_score = score
            self._best_html = html
            self._best_iteration = iteration

        return IterationOutput(
            iteration=iteration,
            html_path=html_path,
            design_spec_path=design_spec_path,
            validation_path=validation_path,
            evaluation_path=evaluation_path,
        )

    def finalize(self, fallback_html: str | None = None) -> Path:
        """Write the selected final HTML and run metadata."""
        html = self._best_html if self._best_html is not None else fallback_html
        if html is None:
            raise ValueError("No HTML result is available to finalize")

        final_path = self.run_dir / "final.html"
        final_path.write_text(html, encoding="utf-8")

        result = {
            "save_mode": self.save_mode,
            "best_iteration": self._best_iteration,
            "best_score": self._best_score,
        }
        self._write_json(self.run_dir / "result.json", result)
        return final_path

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif hasattr(value, "__dataclass_fields__"):
            from dataclasses import asdict

            value = asdict(value)

        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
