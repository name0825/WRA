"""Aggregate saved quality benchmark runs without invoking a model or browser."""

import argparse
import json
import sys
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
            pages.append({
                "page": page,
                "iteration": int(iteration_dir.name[4:]),
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", type=Path)
    args = parser.parse_args()
    log_dir = args.log_dir.resolve()
    runs = []
    for line in (log_dir / "bench.log").read_text(encoding="utf-8").splitlines():
        sample, exit_text, _, run_names = line.split("\t", 3)
        for run_name in run_names.split() or [None]:
            run = aggregate_run(sample, run_name, log_dir)
            run["exit_code"] = int(exit_text.removeprefix("exit="))
            runs.append(run)
    iterations = [item for run in runs for item in run["iterations"]]
    scores = [item["overall_score"] for item in iterations]
    summary = {
        "run_count": len(runs),
        "failed_run_count": sum(run["exit_code"] != 0 for run in runs),
        "iteration_count": len(iterations),
        "average_overall_score": sum(scores) / len(scores) if scores else None,
        "total_le_4_count": sum(item["total_le_4"] for item in iterations),
        "content_gate_le_4_count": sum(item["content_gate_le_4"] is True for item in iterations),
    }
    report = {
        "provenance": {
            "scores": "stored evaluation.json",
            "findings": "recalculated with current validators from saved HTML and browser.json; no rerender",
        },
        "summary": summary,
        "runs": runs,
    }
    (log_dir / "aggregate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mean_text = f"{summary['average_overall_score']:.3f}" if scores else "unavailable"
    lines = ["Scores: stored evaluation.json; findings: current validators on saved HTML/browser.json (no rerender).", f"Runs: {summary['run_count']}; failed: {summary['failed_run_count']}; iterations: {summary['iteration_count']}; mean overall: {mean_text}; total<=4: {summary['total_le_4_count']}; content gate<=4: {summary['content_gate_le_4_count']}"]
    for run in runs:
        scores_text = ", ".join(f"i{item['iteration']}={item['overall_score']:g}" for item in run["iterations"])
        lines.append(f"{run['sample']} {run['run'] or 'no run directory'}: exit={run['exit_code']}; {scores_text or 'no saved evaluations'}; repair log lines={run['repair_log_lines']}")
        for item in run["iterations"]:
            findings = item["visual_findings"]
            counts = ", ".join(f"{name}={len(values)}" for name, values in findings.items())
            mixed = item["foreign_script_mixed_tokens"]
            lines.append(f"  i{item['iteration']}: total<=4={item['total_le_4']}, content_gate<=4={item['content_gate_le_4']}, mixed_tokens={len(mixed) if mixed is not None else 'unavailable'}, {counts}, first_screen_measured={item['first_screen_measurements_available']}, overflow_px={item['viewport_overflow_px']}")
    (log_dir / "aggregate.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
