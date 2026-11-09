import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

from wra.browser.evaluator import _localize_framework_cdns
from wra.browser.framework_cache import DEFAULT_CACHE_DIR
from wra.browser.guard import launch_guarded
from wra.browser.craft_measure import measure_craft
from wra.core.config import load_config
from wra.validator.visual import find_refinable_browser_defects
from wra.browser.models import BrowserEvaluationResult

PREVIEW_NAME = ".craft_preview.html"
CACHE_DIRNAME = ".craft_frameworks"
TIMEOUT_MS = 10000


def _html_files(run_dir: Path, include_iterations: bool) -> list[Path]:
    files = [run_dir / "index.html"] if (run_dir / "index.html").is_file() else []
    if include_iterations:
        files += sorted(run_dir.glob("iterations/iter*/*.html"))
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="*", type=Path)
    parser.add_argument("--index-only", action="store_true")
    args = parser.parse_args()
    skipped: list[str] = []
    frameworks = load_config().frameworks
    runs = args.runs or sorted(Path("output").glob("run_*"))

    counts: Counter[str] = Counter()
    flagged_runs: dict[str, set[str]] = {}
    measured_runs: set[str] = set()
    with sync_playwright() as playwright:
        browser = launch_guarded(playwright)
        try:
            for run_dir in runs:
                for html_file in _html_files(run_dir, not args.index_only):
                    preview = run_dir / PREVIEW_NAME
                    label = f"{run_dir.name}/{html_file.relative_to(run_dir).as_posix()}"
                    try:
                        try:
                            html = _localize_framework_cdns(html_file.read_text(encoding="utf-8"), frameworks, DEFAULT_CACHE_DIR, run_dir, CACHE_DIRNAME)
                        except Exception as exc:
                            print(json.dumps({"file": label, "skipped_cdn": str(exc)[:200]}, ensure_ascii=False), flush=True)
                            skipped.append(label)
                            continue
                        preview.write_text(html, encoding="utf-8")
                        craft = measure_craft(browser, preview.resolve().as_uri(), run_dir.resolve(), TIMEOUT_MS)
                    finally:
                        preview.unlink(missing_ok=True)
                        shutil.rmtree(run_dir / CACHE_DIRNAME, ignore_errors=True)
                    print(json.dumps({"file": label, "craft": craft.model_dump(mode="json") if craft else None}, ensure_ascii=False), flush=True)
                    if craft is None:
                        continue
                    measured_runs.add(run_dir.name)
                    checks = {
                        "js_off_hidden": bool(find_refinable_browser_defects(BrowserEvaluationResult(enabled=True, craft=craft))),
                        "no_first_screen_h1": not craft.first_screen_h1,
                        "no_first_screen_cta": not craft.first_screen_cta,
                        "wrapped_button_label": bool(craft.wrapped_button_labels),
                    }
                    for name, hit in checks.items():
                        if hit:
                            counts[name] += 1
                            flagged_runs.setdefault(name, set()).add(run_dir.name)
        finally:
            browser.close()
    print(json.dumps({"measured_runs": len(measured_runs), "skipped_cdn": skipped,"files_flagged": dict(counts), "runs_flagged": {name: sorted(names) for name, names in flagged_runs.items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
