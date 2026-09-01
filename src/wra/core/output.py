from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
from typing import Any


@dataclass(frozen=True)
class IterationOutput:
    iteration: int
    html_path: Path | None
    design_spec_path: Path | None
    validation_path: Path | None
    evaluation_path: Path | None
    browser_path: Path | None


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
        self._best_browser_source: Path | None = None

    def save_iteration(self, iteration: int, html: str, design_spec: Any | None = None,
                       validation: Any | None = None, evaluation: Any | None = None,
                       browser: Any | None = None, score: float | None = None) -> IterationOutput:
        html_path = design_spec_path = validation_path = evaluation_path = browser_path = None
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
            if browser is not None:
                browser_path = iteration_dir / "browser"
                self._copy_browser_artifacts(browser, browser_path)
                self._write_json(iteration_dir / "browser.json", browser)

        if score is None or self._best_score is None or score > self._best_score:
            self._best_score = score
            self._best_html = html
            self._best_iteration = iteration
            self._best_browser_source = self._browser_source(browser)

        return IterationOutput(iteration, html_path, design_spec_path, validation_path, evaluation_path, browser_path)

    def finalize(self, fallback_html: str | None = None) -> Path:
        html = self._best_html if self._best_html is not None else fallback_html
        if html is None:
            raise ValueError("No HTML result is available to finalize")
        final_path = self.run_dir / "final.html"
        final_path.write_text(html, encoding="utf-8")
        if self.save_mode == "best" and self._best_browser_source is not None:
            browser_dir = self.run_dir / "browser"
            if browser_dir.exists():
                shutil.rmtree(browser_dir)
            shutil.copytree(self._best_browser_source, browser_dir)
        self._write_json(self.run_dir / "result.json", {
            "save_mode": self.save_mode,
            "best_iteration": self._best_iteration,
            "best_score": self._best_score,
        })
        return final_path

    @staticmethod
    def _browser_source(browser: Any | None) -> Path | None:
        if browser is None or not getattr(browser, "enabled", False):
            return None
        paths = [getattr(viewport, "screenshot_path", None) for viewport in getattr(browser, "viewports", [])]
        valid = [Path(path) for path in paths if path]
        return valid[0].parent if valid else None

    @staticmethod
    def _copy_browser_artifacts(browser: Any | None, destination: Path) -> Path | None:
        source = OutputManager._browser_source(browser)
        if source is None or not source.exists():
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        return destination

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif hasattr(value, "__dataclass_fields__"):
            value = asdict(value)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
