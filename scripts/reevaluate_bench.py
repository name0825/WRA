"""Re-score already-saved benchmark iterations with the current evaluator model.

Reuses the saved HTML, FeatureModel, DesignSpec and browser.json (screenshot
paths pointed at the copied PNGs) without regenerating or re-rendering, to
check whether visual judgments change now that the evaluator can see
screenshots.

Usage: reevaluate_bench.py BENCH_DIR [--out DIR] [--limit N] [--dry-run] [--force-resume]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wra.browser.models import BrowserEvaluationResult
from wra.core.config import load_config
from wra.core.models import EVALUATION_DIMENSIONS, FeatureModel
from wra.cli import _resolve_seed, build_visual_inspector
from wra.designer.schema import DesignSpec
from wra.evaluator.llm import LLMEvaluator
from wra.llm.model_selector import ModelSelector
from wra.llm.ollama import OllamaClient
from wra.llm.vision_capability import warn_if_vision_missing
from wra.utils.logging import configure_logging
from wra.validator.anchors import find_broken_anchors
from wra.validator.browser_errors import find_browser_errors
from wra.validator.syntax import find_syntax_defects
from wra.validator.tokens import find_corrupted_tokens
from wra.validator.visual import find_visual_defects

QUOTE_RE = re.compile(r"'([^']{4,60})'")
FINGERPRINT_NAME = "fingerprint.json"
PROMPT_FILES = ("llm.md", "visual_check.md")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_browser_result(browser_json_path: Path, data_dir: Path) -> BrowserEvaluationResult | None:
    if not browser_json_path.exists():
        return None
    browser_result = BrowserEvaluationResult.model_validate(read_json(browser_json_path))
    fixed_viewports = []
    for viewport in browser_result.viewports:
        png_path = data_dir / "browser" / f"{viewport.name}.png"
        screenshot_path = str(png_path) if png_path.exists() else viewport.screenshot_path
        fixed_viewports.append(viewport.model_copy(update={"screenshot_path": screenshot_path}))
    return browser_result.model_copy(update={"viewports": fixed_viewports})


def discover_iterations(bench_dir: Path) -> tuple[list[dict], list[str]]:
    iterations = []
    errors = []
    bench_log = bench_dir / "bench.log"
    for line in bench_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample, _exit_text, _elapsed, run_names_text = line.split("\t", 3)
        for run_name in run_names_text.split() or [None]:
            if not run_name:
                errors.append(f"{sample}: no run directory recorded")
                continue
            run_dir = ROOT / "output" / run_name
            result_path = run_dir / "result.json"
            if not result_path.exists():
                errors.append(f"{sample}/{run_name}: missing result.json")
                continue
            result = read_json(result_path)
            for page in result.get("pages", {}):
                feature_path = run_dir / "data" / page / "features.json"
                if not feature_path.exists():
                    errors.append(f"{sample}/{run_name}/{page}: missing features.json")
                    continue
                try:
                    features = FeatureModel.model_validate(read_json(feature_path))
                except Exception as exc:
                    errors.append(f"{sample}/{run_name}/{page}: failed to load features.json: {exc}")
                    continue
                iterations_dir = run_dir / "iterations"
                for iteration_dir in sorted(iterations_dir.glob("iter*"), key=lambda path: int(path.name[4:])):
                    iteration = int(iteration_dir.name[4:])
                    data_dir = iteration_dir / "data" / page
                    html_path = iteration_dir / f"{page}.html"
                    design_path = data_dir / "design.json"
                    evaluation_path = data_dir / "evaluation.json"
                    browser_json_path = data_dir / "browser.json"
                    if not html_path.exists() or not evaluation_path.exists() or not design_path.exists():
                        errors.append(f"{sample}/{run_name}/{page}/iter{iteration}: missing html/design.json/evaluation.json")
                        continue
                    try:
                        html = html_path.read_text(encoding="utf-8")
                        design_spec = DesignSpec.model_validate(read_json(design_path))
                        old_evaluation = read_json(evaluation_path)
                        browser_result = load_browser_result(browser_json_path, data_dir)
                    except Exception as exc:
                        errors.append(f"{sample}/{run_name}/{page}/iter{iteration}: {exc}")
                        continue
                    iterations.append({
                        "sample": sample,
                        "run_name": run_name,
                        "page": page,
                        "iteration": iteration,
                        "features": features,
                        "design_spec": design_spec,
                        "html": html,
                        "browser_result": browser_result,
                        "old_evaluation": old_evaluation,
                        "data_dir": data_dir,
                    })
    return iterations, errors


def absent_quotes(findings: list[str], html: str) -> int:
    count = 0
    for finding in findings:
        for quoted in QUOTE_RE.findall(finding):
            if quoted not in html:
                count += 1
    return count


def git_output(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True, timeout=10).stdout


def code_version() -> dict | None:
    try:
        commit = git_output("rev-parse", "HEAD").decode().strip()
        diff = git_output("diff", "HEAD", "--", "src", "prompts")
        untracked = git_output("ls-files", "--others", "--exclude-standard", "--", "src", "prompts").decode().splitlines()
        digest = hashlib.sha256(diff)
        for name in sorted(untracked):
            digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return {"commit": commit, "dirty_hash": digest.hexdigest() if diff or untracked else None}


def build_fingerprint(config, model) -> dict:
    prompt_hashes = {name: hashlib.sha256((ROOT / "prompts" / "evaluator" / name).read_bytes()).hexdigest() for name in PROMPT_FILES}
    return {
        "evaluator_model": model.name,
        "think": model.think,
        "timeout": model.timeout,
        "temperature": config.ollama.temperature,
        "seed": config.ollama.seed,
        "recovery": config.recovery.model_dump(mode="json"),
        "content_freedom": config.content.freedom,
        "evaluation_max_pixels": config.browser.evaluation_max_pixels,
        "prompts": prompt_hashes,
        "code": code_version(),
    }


def write_fingerprint(path: Path, fingerprint: dict) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp_path, path)


def check_fingerprint(out_dir: Path, fingerprint: dict, force: bool) -> None:
    path = out_dir / FINGERPRINT_NAME
    if path.exists():
        previous = read_json(path)
    elif any(out_dir.glob("*_i*.json")):
        previous = None
    else:
        previous = fingerprint
    if previous != fingerprint and not force:
        changed = sorted(key for key in {**fingerprint, **(previous or {})} if previous is None or previous.get(key) != fingerprint.get(key))
        if changed == ["forced"]:
            sys.exit(f"error: {out_dir}: previous run was force-resumed; pass --force-resume again or use a new --out.")
        reason = "has no fingerprint" if previous is None else f"was made with different settings ({', '.join(changed)})"
        sys.exit(f"error: {out_dir} {reason}; resuming would reuse stale scores. Use a new --out, or pass --force-resume to reuse them anyway.")
    write_fingerprint(path, {**fingerprint, "forced": True} if force and previous != fingerprint else fingerprint)


def result_path(out_dir: Path, item: dict) -> Path:
    return out_dir / f"{item['sample']}_{item['run_name']}_{item['page']}_i{item['iteration']}.json"


def build_table(bench_dir: Path, iterations: list[dict], out_dir: Path) -> list[str]:
    header = ["sample", "run", "page", "iter", "old_overall", "new_overall"]
    for dim in EVALUATION_DIMENSIONS:
        header += [f"{dim}_old", f"{dim}_new"]
    header.append("absent_quotes")
    rows = [header]
    old_overalls = []
    new_overalls = []
    for item in iterations:
        out_path = result_path(out_dir, item)
        if not out_path.exists():
            continue
        saved = read_json(out_path)
        new_evaluation = saved["evaluation"]
        old_evaluation = item["old_evaluation"]
        row = [item["sample"], item["run_name"], item["page"], str(item["iteration"]),f"{old_evaluation['overall_score']:g}", f"{new_evaluation['overall_score']:g}"]
        for dim in EVALUATION_DIMENSIONS:
            row += [f"{old_evaluation[dim]['score']:g}", f"{new_evaluation[dim]['score']:g}"]
        row.append(str(absent_quotes(saved["inspection_findings"], item["html"])))
        rows.append(row)
        old_overalls.append(old_evaluation["overall_score"])
        new_overalls.append(new_evaluation["overall_score"])
    if old_overalls:
        mean_row = ["MEAN", "", "", "", f"{sum(old_overalls) / len(old_overalls):.3f}", f"{sum(new_overalls) / len(new_overalls):.3f}"]
        mean_row += [""] * (len(header) - len(mean_row) - 1)
        mean_row.append("")
        rows.append(mean_row)
    lines = ["\t".join(row) for row in rows]
    (out_dir / "summary.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench_dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-resume", action="store_true")
    args = parser.parse_args()
    bench_dir = args.bench_dir.resolve()

    iterations, errors = discover_iterations(bench_dir)
    for error in errors:
        print(f"load error: {error}")

    if args.dry_run:
        print(f"Prepared {len(iterations)} iterations, {len(errors)} load errors.")
        return

    config = load_config()
    configure_logging(level=config.logging.level, console_level=config.logging.console_level, log_dir=config.logging.directory)
    client = OllamaClient(config.ollama.base_url, keep_alive=config.ollama.keep_alive, temperature=config.ollama.temperature, seed=_resolve_seed(config.ollama.seed))
    warn_if_vision_missing(config.ollama.base_url, "evaluator", [model.name for model in config.models.evaluator])
    em = ModelSelector.select(config.models.evaluator, "evaluator")
    rec = config.recovery
    evaluator = LLMEvaluator(llm_client=client, model="", min_num_ctx=rec.initial_num_ctx, max_num_ctx=rec.max_num_ctx, ceiling_num_ctx=rec.initial_ceiling_num_ctx, max_retries=rec.max_retries, content_freedom=config.content.freedom, growth_power=rec.growth_power, screenshot_max_pixels=config.browser.evaluation_max_pixels)
    visual_inspector = build_visual_inspector(config, client)

    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", em.name)
    out_dir = args.out.resolve() if args.out else bench_dir / f"reeval_{model_slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    check_fingerprint(out_dir, build_fingerprint(config, em), args.force_resume)

    to_process = iterations[: args.limit] if args.limit is not None else iterations
    for item in to_process:
        out_path = result_path(out_dir, item)
        if out_path.exists():
            continue
        started = time.monotonic()
        features = item["features"]
        design_spec = item["design_spec"]
        html = item["html"]
        browser_result = item["browser_result"]
        inspection_findings = visual_inspector.inspect(browser_result, html, model=em.name, think=em.think, timeout=em.timeout)
        evaluation = evaluator.evaluate(
            features, design_spec, html, browser_result=browser_result,
            content_findings=find_corrupted_tokens(html, features),
            ux_findings=find_broken_anchors(html) + find_browser_errors(browser_result) + find_syntax_defects(html),
            visual_findings=find_visual_defects(html, features, browser_result) + inspection_findings,
            model=em.name, think=em.think, timeout=em.timeout,
        )
        out_path.write_text(json.dumps({
            "evaluation": evaluation.model_dump(mode="json"),
            "inspection_findings": inspection_findings,
            "old_overall": item["old_evaluation"]["overall_score"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed = time.monotonic() - started
        print(f"{item['sample']} i{item['iteration']}: old={item['old_evaluation']['overall_score']:g} new={evaluation.overall_score:g} elapsed={elapsed:.1f}s", flush=True)

    lines = build_table(bench_dir, iterations, out_dir)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
