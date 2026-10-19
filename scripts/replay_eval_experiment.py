"""Replay saved reference-polished iterations through visual inspection and evaluation under num_ctx/screenshot-size conditions.

Usage: replay_eval_experiment.py OUT_DIR [--items N] [--limit-trials M] [--dry-run]
"""

import argparse
import hashlib
import json
import logging
import os
import re
import struct
import sys
import time
from datetime import datetime
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from wra.cli import _resolve_seed
from wra.core.config import load_config
from wra.core.models import FeatureModel
from wra.designer.schema import DesignSpec
from wra.evaluator.llm import LLMEvaluator
from wra.evaluator.visual_check import VisualInspector
from wra.llm.model_selector import ModelSelector
from wra.llm.ollama import OllamaClient
from wra.validator.anchors import find_broken_anchors
from wra.validator.browser_errors import find_browser_errors
from wra.validator.syntax import find_syntax_defects
from wra.validator.tokens import find_corrupted_tokens
from wra.validator.visual import find_visual_defects

from reevaluate_bench import load_browser_result, read_json

VIEWPORTS = ("desktop", "tablet", "mobile")
STANDARD_WIDTHS = (1440, 768, 390)
CONDITIONS = {"A": (32768, None), "B": (65536, None), "C": (32768, None), "D": (32768, lambda px: 4_000_000), "E": (32768, lambda px: 8_000_000), "M": (32768, lambda px: px // 2), "H": (32768, lambda px: px // 4)}
PROBE_PROMPT = "Reply with the single word: pong"
RECOVERY_MAX_PROBES = 6
RECOVERY_SLEEP_SECS = 10
SEED_STRIDE = 1000
SEED_SCHEME = f"trial_stride_{SEED_STRIDE}"


class TrialSeededClient(OllamaClient):
    def begin_trial(self, trial: int) -> None:
        with self._seed_lock:
            self._seed_counter = trial * SEED_STRIDE
logger = logging.getLogger("replay_eval_experiment")


def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as handle:
        header = handle.read(24)
    return struct.unpack(">II", header[16:24])


def pixel_sum(paths) -> int:
    return sum(w * h for w, h in (png_size(Path(path)) for path in paths))


def discover_items(samples: tuple[str, ...] = ("reference-polished",)) -> list[dict]:
    items = {}
    for bench_log in sorted((ROOT / "bench").glob("quality_baseline_*/bench.log")):
        for line in bench_log.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t", 3)
            if len(fields) < 4 or fields[0] not in samples:
                continue
            for run in fields[3].split():
                run_dir = ROOT / "output" / run
                for iteration_dir in sorted((run_dir / "iterations").glob("iter*")):
                    if not iteration_dir.name[4:].isdigit():
                        continue
                    iteration = int(iteration_dir.name[4:])
                    for data_dir in sorted((iteration_dir / "data").glob("*")):
                        page = data_dir.name
                        pngs = [data_dir / "browser" / f"{name}.png" for name in VIEWPORTS]
                        required = [iteration_dir / f"{page}.html", data_dir / "design.json", data_dir / "browser.json", run_dir / "data" / page / "features.json", *pngs]
                        if (run, iteration, page) in items or not all(path.exists() for path in required):
                            continue
                        items[(run, iteration, page)] ={"sample": fields[0], "bench_dir": bench_log.parent.name, "run": run, "iter": iteration, "page": page, "iteration_dir": iteration_dir, "data_dir": data_dir, "features_path": run_dir / "data" / page / "features.json", "px": pixel_sum(pngs), "widths": tuple(png_size(path)[0] for path in pngs)}
    return sorted(items.values(), key=lambda item: item["px"])


def select_items(all_items: list[dict], px_min: int, px_max: int, count: int, keep=None) -> tuple[list[dict], list[dict]]:
    candidates = [item for item in all_items if px_min <= item["px"] <= px_max and item["widths"] == STANDARD_WIDTHS and (keep is None or keep(item))]
    if len(candidates) <= count:
        chosen = candidates
    else:
        chosen = [candidates[round(j * (len(candidates) - 1) / (count - 1))] for j in range(count)] if count > 1 else candidates[:1]
    half = (len(chosen) + 1) // 2
    ordered = [item for pair in zip_longest(chosen[:half], chosen[half:]) for item in pair if item is not None]
    return candidates, ordered


class HalfSizer:
    def __init__(self, half_dir: Path) -> None:
        self._half_dir = half_dir
        self._playwright = None
        self._browser = None
        self._page = None

    def _shoot(self, source: Path, target: Path, width: int, height: int) -> None:
        if self._page is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
            self._page = self._browser.new_page()
        holder = self._half_dir / "_half_render.html"
        holder.write_text(f'<!doctype html><html><body style="margin:0"><img src="{source.as_uri()}" style="display:block;width:{width}px;height:{height}px"></body></html>', encoding="utf-8")
        self._page.set_viewport_size({"width": width, "height": height})
        self._page.goto(holder.as_uri(), wait_until="load")
        self._page.screenshot(path=str(target))

    def half(self, item: dict, name: str) -> Path:
        self._half_dir.mkdir(parents=True, exist_ok=True)
        source = item["data_dir"] / "browser" / f"{name}.png"
        target = self._half_dir / f"{item['run']}_iter{item['iter']}_{item['page']}_{name}.png"
        width, height = png_size(source)
        want = (width // 2, height // 2)
        if target.exists() and all(abs(a - b) <= 1 for a, b in zip(png_size(target), want)):
            return target
        self._shoot(source, target, *want)
        got = png_size(target)
        if not all(abs(a - b) <= 1 for a, b in zip(got, want)):
            raise RuntimeError(f"half-size render {target.name} is {got}, wanted {want}")
        return target

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._playwright.stop()


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def summary(self) -> dict:
        failures = [m for m in self.messages if m.startswith("LLM transport failure:")]
        kinds = [match.group(1) for m in failures if (match := re.search(r"kind=(\S+)", m))]
        waits = [float(match.group(1)) for m in self.messages if m.startswith("LLM transport recovery:") and (match := re.search(r"ready after ([\d.]+)s", m))]
        request = next((m for m in self.messages if m.startswith("LLM request:")), "")
        received = next((m for m in self.messages if m.startswith("LLM response received:")), "")

        def grab(pattern: str, text: str):
            match = re.search(pattern, text)
            return int(match.group(1)) if match else None

        not_run = next((m for m in self.messages if m.startswith("Visual inspection did not run:")), None)
        return {
            "transport_failures": len(failures),
            "failure_kinds": kinds,
            "recovery_waits": waits,
            "prompt_chars": grab(r"prompt_chars=(\d+)", request),
            "prompt_tokens": grab(r"prompt_tokens=(\d+)", received),
            "eval_tokens": grab(r"eval_tokens=(\d+)", received),
            "ctx_exceeded": any(m.startswith("LLM context window exceeded") for m in self.messages),
            "visual_error": not_run.split("error=", 1)[-1][:200] if not_run else None,
        }


class timed_capture:
    def __enter__(self):
        self.capture = Capture()
        self.loggers = [logging.getLogger("wra.llm.ollama"), logging.getLogger("wra.evaluator.visual_check"), logging.getLogger("wra.evaluator.screenshots")]
        for log in self.loggers:
            log.addHandler(self.capture)
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc_info):
        self.secs = round(time.monotonic() - self.started, 1)
        for log in self.loggers:
            log.removeHandler(self.capture)
        return False


def call_record(timer: timed_capture, error: str | None) -> dict:
    summary = timer.capture.summary()
    error = error or summary.pop("visual_error")
    summary.pop("visual_error", None)
    return {"ok": error is None, "first_send_failed": summary["transport_failures"] >= 1 or error is not None, **summary, "secs": timer.secs, "error": error}


def ping(client: OllamaClient, model: str, num_ctx: int, timeout: float) -> None:
    try:
        client.generate(prompt=PROBE_PROMPT, model=model, options={"num_predict": 8, "num_ctx": num_ctx}, timeout=timeout)
    except RuntimeError as exc:
        if "output token limit" not in str(exc):
            raise


def is_permanent_failure(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 and "No instance found" not in str(exc)


def wait_until_responsive(client: OllamaClient, model: str) -> float | None:
    started = time.monotonic()
    for attempt in range(1, RECOVERY_MAX_PROBES + 1):
        try:
            ping(client, model, 32768, 30)
            return round(time.monotonic() - started, 1)
        except Exception as exc:
            logger.warning("Waiting for the model after a failed call: %s", exc)
            print(f"waiting for {model}: probe {attempt}/{RECOVERY_MAX_PROBES} failed after {time.monotonic() - started:.0f}s ({str(exc)[:100]})", flush=True)
            if is_permanent_failure(exc):
                return None
            if attempt < RECOVERY_MAX_PROBES:
                time.sleep(RECOVERY_SLEEP_SECS)
    return None


def run_probe(client: OllamaClient, model: str, out_dir: Path) -> None:
    for number, num_ctx in enumerate([32768, 65536] * 3, 1):
        started = time.monotonic()
        error = None
        try:
            ping(client, model, num_ctx, 120)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:200]
        row = {"probe": number, "num_ctx": num_ctx, "secs": round(time.monotonic() - started, 1), "ok": error is None, "error": error}
        with open(out_dir / "probe.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"probe {number}: num_ctx={num_ctx} secs={row['secs']} ok={row['ok']}", flush=True)


def build_schedule(items: list[dict], arms: str = "ABC", repeats: int = 1) -> list[dict]:
    schedule = []
    for p in range(len(arms) * repeats):
        for i, item in enumerate(items):
            schedule.append({"trial": len(schedule) + 1, "pass": p, "condition": arms[(i + p) % len(arms)], "item": item})
    return schedule


def trial_limits(entry: dict) -> tuple[int, int | None]:
    num_ctx, eval_cap = CONDITIONS[entry["condition"]]
    return num_ctx, eval_cap(entry["item"]["px"]) if eval_cap else None


def prepare_trial(entry: dict, sizer: HalfSizer):
    item = entry["item"]
    browser_result = load_browser_result(item["data_dir"] / "browser.json", item["data_dir"])
    if entry["condition"] == "C":
        halves = {name: str(sizer.half(item, name)) for name in VIEWPORTS}
        browser_result = browser_result.model_copy(update={"viewports": [v.model_copy(update={"screenshot_path": halves.get(v.name, v.screenshot_path)}) for v in browser_result.viewports]})
    features = FeatureModel.model_validate(read_json(item["features_path"]))
    design_spec = DesignSpec.model_validate(read_json(item["data_dir"] / "design.json"))
    html = (item["iteration_dir"] / f"{item['page']}.html").read_text(encoding="utf-8")
    return browser_result, features, design_spec, html


def build_evaluator(client, config, num_ctx: int, screenshot_max_pixels: int | None = None) -> LLMEvaluator:
    return LLMEvaluator(llm_client=client, model="", min_num_ctx=num_ctx, max_num_ctx=num_ctx, ceiling_num_ctx=num_ctx, max_retries=0, content_freedom=config.content.freedom, growth_power=config.recovery.growth_power, screenshot_max_pixels=screenshot_max_pixels)


def is_clean(item: dict, model) -> bool:
    browser_result, features, design_spec, html = prepare_trial({"item": item, "condition": "A"}, None)
    found = evaluate_args(browser_result, features, html, [], model)
    return not (found["content_findings"] or found["ux_findings"] or found["visual_findings"])


def evaluate_args(browser_result, features, html, visual_findings, model):
    return dict(
        browser_result=browser_result,
        content_findings=find_corrupted_tokens(html, features),
        ux_findings=find_broken_anchors(html) + find_browser_errors(browser_result) + find_syntax_defects(html),
        visual_findings=find_visual_defects(html, features, browser_result) + visual_findings,
        model=model.name, think=model.think, timeout=model.timeout,
    )


class PromptSizeProbe:
    prompt = ""
    images = 0

    def generate(self, prompt, model, options=None, images=None, **kwargs):
        PromptSizeProbe.prompt = prompt
        PromptSizeProbe.images = len(images or [])
        raise RuntimeError("dry-run")


def run_trial(entry: dict, client, config, model, sizer: HalfSizer, visual: bool = True) -> dict:
    item = entry["item"]
    num_ctx, eval_cap = trial_limits(entry)
    browser_result, features, design_spec, html = prepare_trial(entry, sizer)
    row = {"trial": entry["trial"], "pass": entry["pass"], "condition": entry["condition"], "sample": item["sample"], "num_ctx": num_ctx, "bench_dir": item["bench_dir"], "run": item["run"], "iter": item["iter"], "page": item["page"], "px_sum": pixel_sum([v.screenshot_path for v in browser_result.viewports]), "started": datetime.now().isoformat(timespec="seconds")}

    findings = []
    row["visual"] = None
    if visual:
        inspector = VisualInspector(client, num_ctx=num_ctx)
        with timed_capture() as timer:
            findings = inspector.inspect(browser_result, html, model=model.name, think=model.think, timeout=model.timeout)
        row["visual"] = call_record(timer, None)
        if not row["visual"]["ok"]:
            row["visual"]["probe_wait_secs"] = wait_until_responsive(client, model.name)
            row["visual"]["recovered"] = row["visual"]["probe_wait_secs"] is not None

    evaluator = build_evaluator(client, config, num_ctx, eval_cap)
    error = None
    evaluation = None
    with timed_capture() as timer:
        try:
            evaluation = evaluator.evaluate(features, design_spec, html, **evaluate_args(browser_result, features, html, findings, model))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:200]
    row["eval"] = call_record(timer, error)
    downscaled = next((re.search(r"->(\d+)", m) for m in timer.capture.messages if m.startswith("Evaluation screenshots downscaled:")), None)
    row["eval_px_sum"] = int(downscaled.group(1)) if downscaled else row["px_sum"]
    if evaluation is not None:
        row["eval"]["overall_score"] = evaluation.overall_score
        row["eval"]["result"] = evaluation.model_dump(mode="json")
    else:
        row["eval"]["probe_wait_secs"] = wait_until_responsive(client, model.name)
        row["eval"]["recovered"] = row["eval"]["probe_wait_secs"] is not None
    row["finished"] = datetime.now().isoformat(timespec="seconds")
    return row


def execute_trial(entry: dict, client, config, model, sizer: HalfSizer, visual: bool = True) -> dict:
    try:
        return run_trial(entry, client, config, model, sizer, visual=visual)
    except Exception as exc:
        item = entry["item"]
        now = datetime.now().isoformat(timespec="seconds")
        error = f"{type(exc).__name__}: {exc}"[:200]
        logger.exception("Trial %d failed", entry["trial"])
        failure = {"ok": False, "first_send_failed": True, "secs": 0, "prompt_tokens": 0, "error": error}
        return {"trial": entry["trial"], "pass": entry["pass"], "condition": entry["condition"], "sample": item["sample"], "num_ctx": trial_limits(entry)[0], "bench_dir": item["bench_dir"], "run": item["run"], "iter": item["iter"], "page": item["page"], "px_sum": item["px"], "started": now, "visual": None, "eval": failure, "eval_px_sum": item["px"], "finished": now}


def evaluation_settings(config, model, schedule: list[dict]) -> dict:
    limits = {}
    for entry in schedule:
        limits.setdefault(entry["condition"], set()).add(trial_limits(entry))
    conditions = {name: [list(pair) for pair in sorted(pairs, key=lambda pair: (pair[0], pair[1] or 0))] for name, pairs in sorted(limits.items())}
    prompts = {f"prompt_sha256:{name}": hashlib.sha256((ROOT / "prompts" / "evaluator" / name).read_bytes()).hexdigest() for name in ("llm.md", "visual_check.md")}
    return {"think": model.think, "content_freedom": config.content.freedom, "temperature": config.ollama.temperature, "seed": config.ollama.seed, "growth_power": config.recovery.growth_power, "base_url": config.ollama.base_url, "seed_scheme": SEED_SCHEME, "conditions": conditions, **prompts}


def schedule_signature(schedule: list[dict], model_name: str, no_visual: bool, settings: dict) -> dict:
    return {"model": model_name, "no_visual": no_visual, "settings": settings, "trials": [{"trial": e["trial"], "pass": e["pass"], "condition": e["condition"], "run": e["item"]["run"], "iter": e["item"]["iter"], "page": e["item"]["page"]} for e in schedule]}


def load_done(out_dir: Path, schedule: list[dict], model_name: str, no_visual: bool, settings: dict) -> set:
    results_path = out_dir / "results.jsonl"
    schedule_path = out_dir / "schedule.json"
    signature = schedule_signature(schedule, model_name, no_visual, settings)
    lines = [line for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()] if results_path.exists() else []
    if schedule_path.exists():
        saved = json.loads(schedule_path.read_text(encoding="utf-8"))
        if saved != signature:
            if lines:
                if "settings" not in saved:
                    raise SystemExit(f"Refusing to resume: {schedule_path} records no evaluation settings, so its results cannot be matched to the current ones. Start a fresh output folder.")
                differing = sorted(key for key in {*saved["settings"], *settings} if saved["settings"].get(key) != settings.get(key))
                if differing:
                    raise SystemExit(f"Refusing to resume: evaluation settings differ from those persisted in {schedule_path}: {', '.join(differing)}. Use a new output folder.")
                raise SystemExit(f"Refusing to resume: the schedule persisted in {schedule_path} differs from the current inputs/options. Use a new output folder.")
            schedule_path.write_text(json.dumps(signature, ensure_ascii=False), encoding="utf-8")
    elif lines:
        raise SystemExit(f"Refusing to resume: {results_path} has results but {schedule_path} is missing, so they cannot be matched to the current schedule. Use a new output folder.")
    else:
        schedule_path.write_text(json.dumps(signature, ensure_ascii=False), encoding="utf-8")
    return {json.loads(line)["trial"] for line in lines}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--items", type=int, default=17)
    parser.add_argument("--px-min", type=int, default=16_000_000)
    parser.add_argument("--px-max", type=int, default=20_000_000)
    parser.add_argument("--limit-trials", type=int)
    parser.add_argument("--conditions")
    parser.add_argument("--samples", default="reference-polished")
    parser.add_argument("--arms", default="ABC")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--clean-only", action="store_true")
    parser.add_argument("--no-visual", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    samples = tuple(args.samples.split(","))
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)

    handler = logging.FileHandler(out_dir / "experiment.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    config = load_config()
    model = ModelSelector.select(config.models.evaluator, "evaluator")

    all_items = discover_items(samples)
    candidates, items = select_items(all_items, args.px_min, args.px_max, args.items, keep=(lambda item: is_clean(item, model)) if args.clean_only else None)
    print(f"{len(all_items)} {args.samples} iterations, {len(candidates)} in {args.px_min}-{args.px_max} px with widths {STANDARD_WIDTHS}; chosen {len(items)} (schedule order)", flush=True)
    for rank, item in enumerate(items):
        print(f"  {rank:2d} {item['px']:>10d} {item['run']} iter{item['iter']} {item['page']} ({item['bench_dir']})", flush=True)
    schedule = build_schedule(items, args.arms, args.repeats)

    sizer = HalfSizer(out_dir / "half")

    try:
        if args.dry_run:
            print("schedule head: " + " ".join(f"{e['trial']}:{e['condition']}@{e['item']['run'][-6:]}i{e['item']['iter']}/{e['item']['page']}" for e in schedule[:9]), flush=True)
            first_c = next((e for e in schedule if e["condition"] == "C"), None)
            if first_c is not None:
                browser_result, features, design_spec, html = prepare_trial(first_c, sizer)
                for viewport in browser_result.viewports:
                    original = first_c["item"]["data_dir"] / "browser" / f"{viewport.name}.png"
                    print(f"trial {first_c['trial']} {viewport.name}: original {png_size(original)} -> half {png_size(Path(viewport.screenshot_path))}", flush=True)
            first = schedule[0]
            browser_result, features, design_spec, html = prepare_trial(first, sizer)
            try:
                build_evaluator(PromptSizeProbe(), config, *trial_limits(first)).evaluate(features, design_spec, html, **evaluate_args(browser_result, features, html, [], model))
            except RuntimeError:
                pass
            print(f"trial 1 ({first['condition']}) evaluation prompt: chars={len(PromptSizeProbe.prompt)} images={PromptSizeProbe.images} est_tokens~{len(PromptSizeProbe.prompt) // 3}", flush=True)
            print(f"{len(items)} items, {len(schedule)} trials", flush=True)
            return

        client = TrialSeededClient(config.ollama.base_url, keep_alive=config.ollama.keep_alive, temperature=config.ollama.temperature, seed=_resolve_seed(config.ollama.seed))
        results_path = out_dir / "results.jsonl"
        done = load_done(out_dir, schedule, model.name, args.no_visual, evaluation_settings(config, model, schedule))
        if not done:
            run_probe(client, model.name, out_dir)
        appended = 0
        for entry in schedule:
            if entry["trial"] in done or (args.conditions is not None and entry["condition"] not in args.conditions):
                continue
            if args.limit_trials is not None and appended >= args.limit_trials:
                break
            logger.info("Trial %d/%d: condition=%s run=%s iter=%d page=%s", entry["trial"], len(schedule), entry["condition"], entry["item"]["run"], entry["item"]["iter"], entry["item"]["page"])
            client.begin_trial(entry["trial"])
            row = execute_trial(entry, client, config, model, sizer, visual=not args.no_visual)
            with open(results_path, "a", encoding="utf-8") as out:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            appended += 1
            v, e = row["visual"], row["eval"]
            visual_text = "skipped" if v is None else f"ff={v['first_send_failed']} {v['secs']}s tok={v['prompt_tokens']}"
            print(f"trial {row['trial']}/{len(schedule)} {row['condition']} px={row['px_sum']} visual[{visual_text}] eval[ff={e['first_send_failed']} ok={e['ok']} {e['secs']}s tok={e['prompt_tokens']}]", flush=True)
            if not e.get("recovered", True):
                print(f"model did not recover after trial {row['trial']}; stopping", flush=True)
                break
            time.sleep(5)
    finally:
        sizer.close()


if __name__ == "__main__":
    main()
