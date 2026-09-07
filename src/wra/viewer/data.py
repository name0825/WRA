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
    slug: str
    evaluation: dict[str, Any] | None
    validation: dict[str, Any] | None

    @property
    def name(self) -> str:
        return f"iter{self.number}"

    @property
    def score(self) -> float | None:
        return (self.evaluation or {}).get("overall_score")

    @property
    def valid(self) -> bool | None:
        return None if self.validation is None else self.validation.get("valid")

    @property
    def has_html(self) -> bool:
        return (self.directory / f"{self.slug}.html").is_file()


@dataclass(frozen=True)
class Run:
    run_id: str
    directory: Path
    result: dict[str, Any] | None
    features: dict[str, Any] | None
    iterations: list[Iteration] = field(default_factory=list)
    slug: str = ""
    slugs: list[str] = field(default_factory=list)

    @property
    def best_iteration(self) -> int | None:
        return (self.page_result or {}).get("best_iteration")

    @property
    def best_score(self) -> float | None:
        return (self.page_result or {}).get("best_score")

    @property
    def page_result(self) -> dict[str, Any] | None:
        pages = (self.result or {}).get("pages") or {}
        return pages.get(self.slug) or (self.result if not pages else None)

    @property
    def title(self) -> str:
        return ((self.features or {}).get("metadata") or {}).get("title") or "-"

    @property
    def site_type(self) -> str:
        return ((self.features or {}).get("site") or {}).get("type") or "-"

    @property
    def has_final(self) -> bool:
        return (self.directory / f"{self.slug}.html").is_file()

    @property
    def has_original(self) -> bool:
        return (self.directory / "data" / self.slug / "original.html").is_file()

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


def page_slugs(run_dir: Path, result: dict[str, Any] | None) -> list[str]:
    """The run's pages, entry first: the order the pipeline recorded them in.

    Falling back to the directory listing puts them in alphabetical order, which
    is not the entry page's order, so the recorded entry is pulled to the front
    when the run says which one it is.
    """
    recorded = list(((result or {}).get("pages") or {}).keys())
    data_dir = run_dir / "data"
    found = sorted(item.name for item in data_dir.iterdir() if item.is_dir()) if data_dir.is_dir() else []
    slugs = recorded + [slug for slug in found if slug not in recorded]
    entry = (result or {}).get("entry")
    if entry in slugs:
        slugs = [entry] + [slug for slug in slugs if slug != entry]
    return slugs


def load_iterations(run_dir: Path, slug: str) -> list[Iteration]:
    iterations_dir = run_dir / "iterations"
    if not iterations_dir.is_dir():
        return []
    iterations = []
    for directory in iterations_dir.iterdir():
        if not directory.is_dir() or not directory.name.startswith("iter"):
            continue
        try:
            number = int(directory.name.removeprefix("iter"))
        except ValueError:
            continue
        data_dir = directory / "data" / slug
        if not data_dir.is_dir():
            continue
        iterations.append(Iteration(number, directory, slug, read_json(data_dir / "evaluation.json"), read_json(data_dir / "validation.json")))
    return sorted(iterations, key=lambda item: item.number)


def load_run(output_root: Path, run_id: str, slug: str | None = None) -> Run | None:
    run_dir = safe_run_dir(output_root, run_id)
    if run_dir is None:
        return None
    result = read_json(run_dir / "result.json")
    slugs = page_slugs(run_dir, result)
    selected = slug if slug in slugs else (slugs[0] if slugs else "")
    features = read_json(run_dir / "data" / selected / "features.json") if selected else None
    return Run(run_id, run_dir, result, features, load_iterations(run_dir, selected), selected, slugs)


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
    data_dir = iteration.directory / "data" / iteration.slug
    return [key for key, filename in ARTIFACTS.items() if (data_dir / filename).is_file()]


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
