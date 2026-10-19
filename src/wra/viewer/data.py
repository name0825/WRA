from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Any

SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{number}" for number in range(1, 10)), *(f"lpt{number}" for number in range(1, 10))}
MAX_JSON_BYTES = 20 * 1024 * 1024
DEFAULT_ASSET_FOLDER = "assets"
RESERVED_ASSET_ROOTS = {"final", "original", "iteration", "iterations", "data"}
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
        return _number((self.evaluation or {}).get("overall_score"))

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
        return _number((self.page_result or {}).get("best_iteration"))

    @property
    def best_score(self) -> float | None:
        return _number((self.page_result or {}).get("best_score"))

    @property
    def page_result(self) -> dict[str, Any] | None:
        pages = _pages(self.result)
        page = pages.get(self.slug)
        return page if isinstance(page, dict) and page else (self.result if not pages else None)

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


def is_slug(value: str) -> bool:
    """A slug names a file, never a path."""
    return bool(SLUG_RE.fullmatch(value)) and ".." not in value and not value.endswith(".") and value.split(".", 1)[0].lower() not in WINDOWS_DEVICE_NAMES


def _number(value: Any) -> Any:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return value if math.isfinite(float(value)) else None
    except OverflowError:
        return None


def _pages(result: dict[str, Any] | None) -> dict[str, Any]:
    pages = (result or {}).get("pages")
    return pages if isinstance(pages, dict) else {}


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except (ValueError, RecursionError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def page_slugs(run_dir: Path, result: dict[str, Any] | None) -> list[str]:
    """The run's pages, entry first: the order the pipeline recorded them in.

    Falling back to the directory listing puts them in alphabetical order, which
    is not the entry page's order, so the recorded entry is pulled to the front
    when the run says which one it is.
    """
    recorded = [slug for slug in _pages(result) if is_slug(slug)]
    data_dir = run_dir / "data"
    found = sorted(item.name for item in data_dir.iterdir() if item.is_dir() and is_slug(item.name)) if data_dir.is_dir() else []
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
    if not run_id or ":" in run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        return None
    run_dir = output_root / run_id
    if not run_dir.is_dir() or run_dir.resolve().parent != output_root.resolve():
        return None
    return run_dir


def asset_folder(run_dir: Path) -> str:
    """The folder under the run that holds its media, as the run recorded it.

    A run directory is not trusted, so a recorded value that is not a plain
    relative folder resolving inside the run falls back to `assets`.
    """
    recorded = (read_json(run_dir / "result.json") or {}).get("asset_directory")
    if not isinstance(recorded, str) or not recorded or not recorded.isprintable() or recorded[0] in "/\\" or ":" in recorded or "\\" in recorded:
        return DEFAULT_ASSET_FOLDER
    parts = recorded.split("/")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) or part.startswith(" ") for part in parts):
        return DEFAULT_ASSET_FOLDER
    if parts[0].lower() in RESERVED_ASSET_ROOTS or parts[0].startswith("."):
        return DEFAULT_ASSET_FOLDER
    base = run_dir.resolve()
    try:
        resolved = (base / recorded).resolve()
    except (OSError, ValueError):
        return DEFAULT_ASSET_FOLDER
    if base not in resolved.parents:
        return DEFAULT_ASSET_FOLDER
    root = resolved.relative_to(base).parts[0]
    return DEFAULT_ASSET_FOLDER if root.lower() in RESERVED_ASSET_ROOTS or root.startswith(".") else recorded


def iteration_artifacts(iteration: Iteration) -> list[str]:
    data_dir = iteration.directory / "data" / iteration.slug
    return [key for key, filename in ARTIFACTS.items() if read_json(data_dir / filename) is not None]


def criteria_scores(evaluation: dict[str, Any] | None) -> list[tuple[str, float | None, str]]:
    if not evaluation:
        return []
    rows = []
    for key in SCORE_KEYS:
        entry = evaluation.get(key)
        if isinstance(entry, dict):
            rows.append((key, _number(entry.get("score")), entry.get("feedback") or ""))
    return rows


def score_band(score: float | None, high: float, mid: float) -> str:
    if score is None:
        return "none"
    if score >= high:
        return "high"
    return "mid" if score >= mid else "low"
