"""Aggregate saved quality benchmark runs without invoking a model or browser."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import EVALUATION_DIMENSIONS, FeatureModel, content_preservation_is_a_gate
from wra.validator.tokens import find_script_mixing_tokens
from wra.validator.visual import (
    find_oversized_header_logo_defects,
    find_render_collapse_defects,
    find_vertical_gap_defects,
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate_run(sample: str, run_name: str | None, log_dir: Path) -> dict:
    run_dir = ROOT / "output" / run_name if run_name else None
    result_path = run_dir / "result.json" if run_dir else None
    result = read_json(result_path) if result_path and result_path.exists() else {"pages": {}}
    pages = []
    for page in result["pages"]:
        page_result = result["pages"][page] if isinstance(result["pages"], dict) else None
        best_iteration = page_result.get("best_iteration") if isinstance(page_result, dict) else None
        feature_path = run_dir / "data" / page / "features.json"
        features = FeatureModel.model_validate(read_json(feature_path)) if feature_path.exists() else None
        for iteration_dir in sorted((run_dir / "iterations").glob("iter*"), key=lambda path: int(path.name[4:])):
            data_dir = iteration_dir / "data" / page
            evaluation_path = data_dir / "evaluation.json"
            if not evaluation_path.exists():
                continue
            evaluation = read_json(evaluation_path)
            html = (iteration_dir / f"{page}.html").read_text(encoding="utf-8")
            browser_path = data_dir / "browser.json"
            browser = BrowserEvaluationResult.model_validate(read_json(browser_path)) if browser_path.exists() else None
            content_score = evaluation["content_preservation"]["score"]
            freedom = evaluation.get("content_freedom", "low")
            route_path = data_dir / "route.json"
            route = read_json(route_path) if route_path.exists() else {}
            pages.append({
                "page": page,
                "iteration": int(iteration_dir.name[4:]),
                "selected": None if best_iteration is None else int(iteration_dir.name[4:]) == int(best_iteration),
                "path": route.get("path", "unrecorded"),
                "refine_failed": route.get("refine_failed"),
                "evaluation_mtime": evaluation_path.stat().st_mtime,
                "overall_score": evaluation["overall_score"],
                "criteria": {name: evaluation[name]["score"] for name in EVALUATION_DIMENSIONS},
                "total_le_4": evaluation["overall_score"] <= 4,
                "content_gate_le_4": content_score <= 4 if content_preservation_is_a_gate(freedom) else None,
                "foreign_script_mixed_tokens": find_script_mixing_tokens(html, features) if features else None,
                "visual_findings": {
                    "vertical_gap": find_vertical_gap_defects(browser),
                    "oversized_header_image": find_oversized_header_logo_defects(browser),
                    "render_collapse": find_render_collapse_defects(html, browser),
                },
                "first_screen_measurements_available": any(
                    viewport.width >= 1200 and viewport.first_screen_max_right is not None
                    for viewport in browser.viewports
                ) if browser and browser.enabled else False,
                "viewport_overflow_px": {
                    viewport.name: max(0, viewport.scroll_width - viewport.viewport_width)
                    for viewport in browser.viewports
                } if browser and browser.enabled else None,
            })
    output_path = log_dir / f"run_{sample}.out"
    repair_count = None
    if output_path.exists():
        repair_count = sum("Repaired " in line for line in output_path.read_text(encoding="utf-8", errors="replace").splitlines())
    return {"sample": sample, "run": run_name, "exit_code": None, "repair_log_lines": repair_count, "iterations": pages}


def path_summary(runs: list[dict]) -> dict:
    seen = set()
    groups: dict[str, list[dict]] = {}
    for run in runs:
        for item in run["iterations"]:
            key = (run["run"], item["page"], item["iteration"])
            if key in seen:
                continue
            seen.add(key)
            groups.setdefault(item["path"], []).append(item)
    return {
        path: {
            "count": len(items),
            "mean_overall": sum(item["overall_score"] for item in items) / len(items),
            "refine_failed_count": sum(item["refine_failed"] is True for item in items),
        }
        for path, items in sorted(groups.items())
    }


def path_text(summary: dict) -> str:
    return "; ".join(f"{path} n={value['count']} mean={value['mean_overall']:.3f} refine_failed={value['refine_failed_count']}" for path, value in summary.items()) or "no saved evaluations"


def scores_text(iterations: list[dict]) -> str:
    kept = {item["page"]: item["overall_score"] for item in iterations if item.get("selected")}
    multi_page = len({item["page"] for item in iterations}) > 1
    parts = []
    for item in iterations:
        text = f"{item['page'] + ':' if multi_page else ''}i{item['iteration']}={item['overall_score']:g}"
        if item.get("selected"):
            text += "*"
        elif item.get("selected") is False and item["overall_score"] > kept.get(item["page"], float("inf")):
            text += " (not kept)"
        parts.append(text)
    return ", ".join(parts)


def collect(log_dir: Path) -> tuple[str, list[dict]]:
    meta_path = log_dir / "bench_meta.json"
    mode = read_json(meta_path)["mode"] if meta_path.exists() else "unrecorded"
    runs = []
    for line in (log_dir / "bench.log").read_text(encoding="utf-8").splitlines():
        sample, exit_text, _, run_names = line.split("\t", 3)
        for run_name in run_names.split() or [None]:
            run = aggregate_run(sample, run_name, log_dir)
            run["exit_code"] = int(exit_text.removeprefix("exit="))
            runs.append(run)
    return mode, runs


def write_report(log_dir: Path, mode: str, runs: list[dict]) -> None:
    iterations = [item for run in runs for item in run["iterations"]]
    mtimes = [item["evaluation_mtime"] for item in iterations]
    time_format = "%Y-%m-%d %H:%M:%S"
    score_files = f"{time.strftime(time_format, time.localtime(min(mtimes)))} .. {time.strftime(time_format, time.localtime(max(mtimes)))}" if mtimes else "unavailable"
    findings_computed_at = time.strftime(time_format)
    scores = [item["overall_score"] for item in iterations]
    selected_scores = [item["overall_score"] for item in iterations if item.get("selected")]
    summary = {
        "run_count": len(runs),
        "failed_run_count": sum(run["exit_code"] != 0 for run in runs),
        "iteration_count": len(iterations),
        "average_overall_score": sum(scores) / len(scores) if scores else None,
        "selected_average_overall_score": sum(selected_scores) / len(selected_scores) if selected_scores else None,
        "total_le_4_count": sum(item["total_le_4"] for item in iterations),
        "content_gate_le_4_count": sum(item["content_gate_le_4"] is True for item in iterations),
    }
    paths = path_summary(runs)
    report = {
        "mode": mode,
        "provenance": {
            "scores": "stored evaluation.json",
            "score_runs": sorted({run["run"] for run in runs if run["run"]}),
            "score_files_modified": score_files,
            "findings": "recalculated with current validators from saved HTML and browser.json; no rerender",
            "findings_computed_at": findings_computed_at,
        },
        "summary": summary,
        "path_summary": paths,
        "runs": runs,
    }
    (log_dir / "aggregate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mean_text = f"{summary['average_overall_score']:.3f}" if scores else "unavailable"
    selected_mean = summary["selected_average_overall_score"]
    mean_text += f"; selected mean overall: {selected_mean:.3f} (n={len(selected_scores)}, * = kept iteration)" if selected_mean is not None else "; selected mean overall: unavailable"
    lines = [f"Scores: stored evaluation.json (files written {score_files}); findings: current validators on saved HTML/browser.json (no rerender), computed {findings_computed_at}.", f"Mode: {mode}; paths: {path_text(paths)}", f"Runs:{summary['run_count']}; failed: {summary['failed_run_count']}; iterations: {summary['iteration_count']}; mean overall: {mean_text}; total<=4: {summary['total_le_4_count']}; content gate<=4: {summary['content_gate_le_4_count']}"]
    for run in runs:
        lines.append(f"{run['sample']} {run['run'] or 'no run directory'}: exit={run['exit_code']}; {scores_text(run['iterations']) or 'no saved evaluations'}; repair log lines={run['repair_log_lines']}")
        for item in run["iterations"]:
            findings = item["visual_findings"]
            counts = ", ".join(f"{name}={len(values)}" for name, values in findings.items())
            mixed = item["foreign_script_mixed_tokens"]
            lines.append(f"  i{item['iteration']}: path={item['path']}, refine_failed={item['refine_failed']}, total<=4={item['total_le_4']}, content_gate<=4={item['content_gate_le_4']}, mixed_tokens={len(mixed) if mixed is not None else 'unavailable'}, {counts}, first_screen_measured={item['first_screen_measurements_available']}, overflow_px={item['viewport_overflow_px']}")
    (log_dir / "aggregate.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", type=Path, nargs="+")
    args = parser.parse_args()
    log_dirs = [path.resolve() for path in args.log_dir]
    collected = []
    for log_dir in log_dirs:
        mode, runs = collect(log_dir)
        write_report(log_dir, mode, runs)
        collected.append((log_dir, mode, runs))
    if len(collected) > 1:
        lines = ["mode / path comparison (initial results are not merged across modes)"]
        for log_dir, mode, runs in collected:
            for path, value in path_summary(runs).items():
                lines.append(f"{mode}\t{path}\tn={value['count']}\tmean={value['mean_overall']:.3f}\trefine_failed={value['refine_failed_count']}\t{log_dir.name}")
        (log_dirs[0] / "compare.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
