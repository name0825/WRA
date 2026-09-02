from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

ARTIFACTS = {"design": "design.json", "validation": "validation.json", "evaluation": "evaluation.json", "browser": "browser.json"}
SCORE_KEYS = ("design_completeness", "visual_hierarchy", "design_consistency", "content_preservation", "ux", "readability", "responsive_design", "originality", "html_quality")


@dataclass(frozen=True)
class Iteration:
    number: int
    directory: Path
    evaluation: dict[str, Any] | None
    validation: dict[str, Any] | None

    @property
    def name(self) -> str:
        return f"iteration_{self.number:03d}"

    @property
    def score(self) -> float | None:
        return (self.evaluation or {}).get("overall_score")

    @property
    def valid(self) -> bool | None:
        return None if self.validation is None else self.validation.get("valid")

    @property
    def has_html(self) -> bool:
        return (self.directory / "html.html").is_file()


@dataclass(frozen=True)
class Run:
    run_id: str
    directory: Path
    result: dict[str, Any] | None
    features: dict[str, Any] | None
    iterations: list[Iteration] = field(default_factory=list)

    @property
    def best_iteration(self) -> int | None:
        return (self.result or {}).get("best_iteration")

    @property
    def best_score(self) -> float | None:
        return (self.result or {}).get("best_score")

    @property
    def title(self) -> str:
        return ((self.features or {}).get("metadata") or {}).get("title") or "-"

    @property
    def site_type(self) -> str:
        return ((self.features or {}).get("site") or {}).get("type") or "-"

    @property
    def has_final(self) -> bool:
        return (self.directory / "final.html").is_file()

    @property
    def has_original(self) -> bool:
        return (self.directory / "original.html").is_file()

    @property
    def complete(self) -> bool:
        return self.result is not None and self.has_final


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def load_iterations(run_dir: Path) -> list[Iteration]:
    iterations_dir = run_dir / "iterations"
    if not iterations_dir.is_dir():
        return []
    iterations = []
    for directory in sorted(iterations_dir.iterdir()):
        if not directory.is_dir() or not directory.name.startswith("iteration_"):
            continue
        try:
            number = int(directory.name.split("_")[-1])
        except ValueError:
            continue
        iterations.append(Iteration(number, directory, read_json(directory / "evaluation.json"), read_json(directory / "validation.json")))
    return sorted(iterations, key=lambda item: item.number)


def load_run(output_root: Path, run_id: str) -> Run | None:
    run_dir = safe_run_dir(output_root, run_id)
    if run_dir is None:
        return None
    return Run(run_id, run_dir, read_json(run_dir / "result.json"), read_json(run_dir / "features.json"), load_iterations(run_dir))


def list_runs(output_root: Path) -> list[Run]:
    if not output_root.is_dir():
        return []
    runs = [load_run(output_root, directory.name) for directory in output_root.iterdir() if directory.is_dir()]
    return sorted((run for run in runs if run is not None), key=lambda run: run.run_id, reverse=True)


def safe_run_dir(output_root: Path, run_id: str) -> Path | None:
    if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        return None
    run_dir = output_root / run_id
    return run_dir if run_dir.is_dir() else None


def iteration_artifacts(iteration: Iteration) -> list[str]:
    return [key for key, filename in ARTIFACTS.items() if (iteration.directory / filename).is_file()]


def criteria_scores(evaluation: dict[str, Any] | None) -> list[tuple[str, float | None, str]]:
    if not evaluation:
        return []
    rows = []
    for key in SCORE_KEYS:
        entry = evaluation.get(key)
        if isinstance(entry, dict):
            rows.append((key, entry.get("score"), entry.get("feedback") or ""))
    return rows


def score_band(score: float | None, high: float, mid: float) -> str:
    if score is None:
        return "none"
    if score >= high:
        return "high"
    return "mid" if score >= mid else "low"
