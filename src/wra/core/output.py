from dataclasses import asdict, dataclass
import json
from pathlib import Path
import logging
import re
import shutil
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIRECTORY = "data"
ITERATIONS_DIRECTORY = "iterations"

# Generated pages address media as `<asset_directory>/<file>`, which only
# resolves from the run directory itself. A copy saved deeper in the tree needs
# the same media reached by a longer path, and rewriting the reference beats
# duplicating a megabyte of images into every iteration.
_SRCSET_RE = re.compile(r"""(srcset\s*=\s*)(["'])([^"']*)\2""", re.I)


def reroot_assets(html: str, prefix: str, asset_directory: str = "assets") -> str:
    """Point every `<asset_directory>/...` reference at the run directory from `prefix` deep."""
    if not prefix:
        return html
    asset_root = f"{asset_directory}/"
    escaped = re.escape(asset_root)
    attribute_re = re.compile(rf"""((?:src|href|poster|data-src)\s*=\s*["'])({escaped})""", re.I)
    url_re = re.compile(rf"""(url\(\s*["']?)({escaped})""", re.I)

    def prefix_srcset(match: re.Match[str]) -> str:
        candidates = []
        for candidate in match.group(3).split(","):
            piece = candidate.strip()
            if piece.startswith(asset_root):
                piece = prefix + piece
            if piece:
                candidates.append(piece)
        return f"{match.group(1)}{match.group(2)}{', '.join(candidates)}{match.group(2)}"

    html = attribute_re.sub(lambda match: match.group(1) + prefix + match.group(2), html)
    html = url_re.sub(lambda match: match.group(1) + prefix + match.group(2), html)
    return _SRCSET_RE.sub(prefix_srcset, html)


@dataclass(frozen=True)
class IterationOutput:
    iteration: int
    slug: str
    html_path: Path | None
    design_spec_path: Path | None
    validation_path: Path | None
    evaluation_path: Path | None
    browser_path: Path | None


@dataclass
class _Best:
    iteration: int
    score: float | None
    html: str
    browser_source: Path | None


class OutputManager:
    """Persist a run's artifacts: source material per page, results per iteration.

        run_dir/
          <slug>.html              the best iteration of each page
          assets/                  media shared by every page
          data/<slug>/             that page's source material and best evidence
          iterations/iter<N>/
            <slug>.html            every page as it stood at iteration N
            data/<slug>/           that iteration's design, scores, and validation
    """

    def __init__(self, run_dir: str | Path, save_mode: str, asset_directory: str = "assets") -> None:
        if save_mode not in {"all", "best"}:
            raise ValueError("output.save_mode must be 'all' or 'best'")
        self.run_dir = Path(run_dir)
        self.save_mode = save_mode
        self.asset_directory = asset_directory
        self._best: dict[str, _Best] = {}
        self._order: list[str] = []
        logger.debug("Output manager initialized: run_dir=%s save_mode=%s", self.run_dir, save_mode)

    def register_pages(self, slugs: list[str]) -> None:
        """Record the run's pages, entry first, before any of them is redesigned.

        Written straight away rather than at the end: until it exists, nothing on
        disk says which page is the entry, and a reader is left guessing from
        directory names in alphabetical order.
        """
        self._order = list(slugs)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.write_result()
        logger.info("Run pages registered: %s", self._order)

    def iteration_dir(self, iteration: int) -> Path:
        return self.run_dir / ITERATIONS_DIRECTORY / f"iter{iteration}"

    def page_data_dir(self, slug: str) -> Path:
        return self.run_dir / DATA_DIRECTORY / slug

    def save_iteration(self, iteration: int, slug: str, html: str, design_spec: Any | None = None, validation: Any | None = None, evaluation: Any | None = None, browser: Any | None = None, score: float | None = None) -> IterationOutput:
        logger.info("Saving iteration artifacts: iteration=%d page=%s mode=%s score=%s", iteration, slug, self.save_mode, score if score is not None else "n/a")
        html_path = design_spec_path = validation_path = evaluation_path = browser_path = None
        if self.save_mode == "all":
            iteration_dir = self.iteration_dir(iteration)
            data_dir = iteration_dir / DATA_DIRECTORY / slug
            data_dir.mkdir(parents=True, exist_ok=True)
            html_path = iteration_dir / f"{slug}.html"
            # Two levels below the run directory: iterations/iter<N>/<slug>.html
            html_path.write_text(reroot_assets(html, "../../", self.asset_directory), encoding="utf-8")
            if design_spec is not None:
                design_spec_path = data_dir / "design.json"
                self._write_json(design_spec_path, design_spec)
            if validation is not None:
                validation_path = data_dir / "validation.json"
                self._write_json(validation_path, validation)
            if evaluation is not None:
                evaluation_path = data_dir / "evaluation.json"
                self._write_json(evaluation_path, evaluation)
            if browser is not None:
                browser_path = data_dir / "browser"
                self._copy_browser_artifacts(browser, browser_path)
                self._write_json(data_dir / "browser.json", browser)
            logger.debug("Iteration artifacts saved: %s", iteration_dir)

        best = self._best.get(slug)
        # An iteration with no score failed validation and was never evaluated.
        # It must not replace a scored page: `_publish` writes the best straight
        # to the run root, so accepting it overwrites the good page with a
        # broken one -- and disagrees with the loop, which keeps the old best.
        if best is None or (score is not None and (best.score is None or score > best.score)):
            self._best[slug] = _Best(iteration=iteration, score=score, html=html, browser_source=self._browser_source(browser))
            logger.info("New best iteration for page '%s': iteration=%d score=%s", slug, iteration, score if score is not None else "n/a")
            # Publish now rather than at the end of the run. Termination is
            # normally disabled, so every run ends by being stopped, and a stop
            # that is not a clean interrupt never reaches finalize: a run killed
            # after two hours of iterating left `result.json` holding nulls and
            # no page at the run root.
            self._publish(slug)

        return IterationOutput(iteration, slug, html_path, design_spec_path, validation_path, evaluation_path, browser_path)

    def _publish(self, slug: str, fallback_html: str | None = None) -> Path:
        """Write a page's current best to the run root, with the run summary."""
        best = self._best.get(slug)
        html = best.html if best is not None else fallback_html
        if html is None:
            raise ValueError(f"No HTML result is available to finalize for page '{slug}'")
        final_path = self.run_dir / f"{slug}.html"
        # Written beside `assets/`, so its references need no rerooting.
        final_path.write_text(html, encoding="utf-8")
        if best is not None and best.browser_source is not None:
            browser_dir = self.page_data_dir(slug) / "browser"
            if browser_dir.exists():
                shutil.rmtree(browser_dir)
            browser_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(best.browser_source, browser_dir)
        self.write_result()
        return final_path

    def finalize(self, slug: str, fallback_html: str | None = None) -> Path:
        final_path = self._publish(slug, fallback_html=fallback_html)
        best = self._best.get(slug)
        logger.info("Output finalized: page=%s final=%s best_iteration=%s best_score=%s", slug, final_path, best.iteration if best else None, best.score if best else None)
        return final_path

    def write_result(self) -> Path:
        path = self.run_dir / "result.json"
        order = self._order or list(self._best)
        pages = {}
        for slug in order:
            best = self._best.get(slug)
            pages[slug] = {"best_iteration": best.iteration if best else None, "best_score": best.score if best else None}
        entry = next(iter(pages), None)
        self._write_json(path, {
            "save_mode": self.save_mode,
            "asset_directory": self.asset_directory,
            "pages": pages,
            # The entry page: the first one registered, and the one a reader
            # should be shown by default.
            "entry": entry,
            # Its numbers, repeated for a quick read of the run.
            "best_iteration": pages[entry]["best_iteration"] if entry else None,
            "best_score": pages[entry]["best_score"] if entry else None,
        })
        return path

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
