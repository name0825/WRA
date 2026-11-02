"""Run the quality benchmark: every sample through `redesign` with benchmark config.

config/config.yaml is swapped per sample and always restored from a backup.
Usage: python scripts/run_bench.py <log_dir> [sample ...] [--mode rewrite|refine] [--check]
"""
import argparse
import json
import ollama
import re
import shutil
import subprocess
import sys
import time
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "config.yaml"
MODELS = ROOT / "config" / "models.yaml"
PROBE_INTERVAL = 10.0
PROBE_TIMEOUT = 30.0
PROBE_MAX_WAIT = 3600.0
REDESIGN = Path(sys.executable).parent / "Scripts" / "redesign.exe"
SAMPLES = {
    "saas-product": ROOT / "benchmarks" / "saas-product",
    "bakery": ROOT / "benchmarks" / "bakery",
    "research-lab": ROOT / "benchmarks" / "research-lab",
    "reference-polished": ROOT / "benchmarks" / "reference-polished",
    "portfolio": None,
    "brief": None,
}
COPIED_INPUTS = {
    "portfolio": ("input.html", "Logo.png"),
    "brief": ("input.txt", "Logo.png"),
}
OVERRIDES = [
    (r'(\n  directory: )"[^"]*"', None),
    (r"(\n  max_iterations: )\S+", "3"),
    (r"(\n  evaluation_threshold: )\S+", "10.1"),
    (r"(\n  termination:\n    enabled: )\S+", "true"),
    (r"(\n    patience: )\S+", "0"),
    (r'(\n  freedom: )"[^"]*"', '"madmax"'),
]


def bench_config(text: str, input_dir: Path, mode: str = "rewrite") -> str:
    for pattern, value in OVERRIDES:
        value = value or f'"{input_dir.as_posix()}"'
        text, count = re.subn(pattern, lambda m: m.group(1) + value, text, count=1)
        if count != 1:
            raise SystemExit(f"config key not found: {pattern}")
    refine = "true" if mode == "refine" else "false"
    return re.sub(r"(\n  refine:\n    enabled: )\S+", lambda m: m.group(1) + refine, text, count=1)


def copied_sources(sample: str) -> list[tuple[str, Path]]:
    sources = []
    for name in COPIED_INPUTS[sample]:
        candidates = (ROOT / "input" / f"_{name}", ROOT / "input" / name)
        sources.append((name, next((p for p in candidates if p.exists()), candidates[-1])))
    return sources


def copied_input(log_dir: Path, sample: str) -> Path:
    target = log_dir / f"{sample}_input"
    target.mkdir(parents=True, exist_ok=True)
    for name, source in copied_sources(sample):
        shutil.copy(source, target / name)
    return target


def input_path(log_dir: Path, sample: str) -> Path:
    return SAMPLES[sample] or log_dir / f"{sample}_input"


def check(names: list[str], modes: list[str], log_dir: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from wra.core.config import load_raw
    from wra.core.models import WRAConfig

    original = CONFIG.read_text(encoding="utf-8")
    models = yaml.safe_load(MODELS.read_text(encoding="utf-8"))
    errors = []
    for name in names:
        input_dir = input_path(log_dir, name)
        if SAMPLES[name]:
            if not input_dir.is_dir():
                errors.append(f"{name}: input directory missing: {input_dir}")
            elif not (input_dir / "index.html").is_file():
                errors.append(f"{name}: index.html missing in {input_dir}")
        else:
            for _, source in copied_sources(name):
                if not source.is_file():
                    errors.append(f"{name}: source file missing: {source}")
        for mode in modes:
            text = bench_config(original, input_dir, mode)
            config = WRAConfig.model_validate(load_raw(overrides={"config.yaml": yaml.safe_load(text), "models.yaml": models}))
            pipeline = config.pipeline
            termination = pipeline.termination
            if pipeline.max_iterations != 3:
                errors.append(f"{name}/{mode}: max_iterations={pipeline.max_iterations}")
            if config.content.freedom != "madmax":
                errors.append(f"{name}/{mode}: freedom={config.content.freedom}")
            if pipeline.refine.enabled != (mode == "refine"):
                errors.append(f"{name}/{mode}: refine.enabled={pipeline.refine.enabled}")
            if termination.enabled and not (pipeline.evaluation_threshold > 10 and termination.patience == 0):
                errors.append(f"{name}/{mode}: early termination is effective")
            if Path(config.input.directory) != input_dir:
                errors.append(f"{name}/{mode}: input.directory={config.input.directory}")
            asset_directory = (input_dir / config.input.asset_directory).is_dir()
            single = config.models.single
            print(f"{name}\t{mode}\tfreedom={config.content.freedom}\tmax_iterations={pipeline.max_iterations}\trefine={pipeline.refine.enabled}\tredesign_after_stalls={pipeline.refine.redesign_after_stalls}\ttermination={termination.enabled}/{pipeline.evaluation_threshold}/{termination.patience}\ttemperature={config.ollama.temperature}\tseed={config.ollama.seed}\tviewports={','.join(v.name for v in config.browser.viewports)}\tsingle={single.enabled}:{single.name}\tinput={input_dir}\tasset_directory_exists={asset_directory}")
    if errors:
        raise SystemExit("check failed:\n" + "\n".join(errors))
    print("check ok")


def git_info() -> dict:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.strip()

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "prompts_commit": git("log", "-1", "--format=%H", "--", "prompts"),
        "dirty_files": [line for line in git("status", "--porcelain").splitlines() if line],
    }


def write_bench_meta(log_dir: Path, mode: str, names: list[str]) -> None:
    ollama_settings = yaml.safe_load(MODELS.read_text(encoding="utf-8")).get("ollama", {})
    meta = {
        "mode": mode,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **git_info(),
        "inputs": {name: str(input_path(log_dir, name)) for name in names},
        "ollama_seed": ollama_settings.get("seed"),
        "seed_reproducible": "unverified",
        "models": "see run_<timestamp>/config.json",
    }
    (log_dir / "bench_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe_target() -> tuple[str, str] | None:
    data = yaml.safe_load(MODELS.read_text(encoding="utf-8"))
    single = data["models"].get("single", {})
    if not single.get("enabled"):
        return None
    return data["ollama"]["base_url"], single["name"]


def is_permanent_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status in (401, 403):
        return True
    return status == 404 and re.search(r"\bmodel\s+(['\"]).+?\1\s+not found", str(error), re.IGNORECASE) is not None


def wait_for_model(base_url: str, model: str, client_factory=ollama.Client, sleep=time.sleep, clock=time.monotonic) -> tuple[int, float]:
    client = client_factory(host=base_url, timeout=PROBE_TIMEOUT)
    started = clock()
    failed = 0
    while True:
        try:
            client.generate(model=model, prompt="Reply with the single word: pong", options={"num_predict": 8})
        except Exception as error:
            failed += 1
            print(f"waiting for {model}: probe {failed} failed ({error})", flush=True)
            if is_permanent_error(error):
                raise SystemExit(f"{model} cannot become ready, aborting: {error}")
            if clock() - started >= PROBE_MAX_WAIT:
                raise SystemExit(f"{model} not ready after {PROBE_MAX_WAIT:.0f}s and {failed} failed probes, aborting: {error}")
            sleep(PROBE_INTERVAL)
        else:
            return failed, clock() - started


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("samples", nargs="*")
    parser.add_argument("--mode", choices=("rewrite", "refine"), default="rewrite")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    log_dir = args.log_dir.resolve()
    names = args.samples or list(SAMPLES)
    if args.check:
        check(names, ["rewrite", "refine"], log_dir)
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    write_bench_meta(log_dir, args.mode, names)
    original = CONFIG.read_text(encoding="utf-8")
    backup = log_dir / "config.yaml.bak"
    backup.write_text(original, encoding="utf-8")
    bench_log = log_dir / "bench.log"
    target = probe_target()
    if target is None:
        print("models.single is disabled; not waiting for the model", flush=True)
    try:
        for name in names:
            if target is not None:
                failed, waited = wait_for_model(*target)
                if failed:
                    with open(log_dir / "wait.log", "a", encoding="utf-8") as log:
                        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{name}\tfailed_probes={failed}\twaited={waited:.0f}s\n")
            input_dir = SAMPLES[name] or copied_input(log_dir, name)
            CONFIG.write_text(bench_config(original, input_dir, args.mode), encoding="utf-8")
            before = set((ROOT / "output").glob("run_*"))
            start = time.time()
            with open(log_dir / f"run_{name}.out", "w", encoding="utf-8") as out:
                code = subprocess.call([str(REDESIGN)], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
            runs = sorted(set((ROOT / "output").glob("run_*")) - before)
            line = f"{name}\texit={code}\t{time.time() - start:.0f}s\t{' '.join(r.name for r in runs)}\n"
            with open(bench_log, "a", encoding="utf-8") as log:
                log.write(line)
            CONFIG.write_text(original, encoding="utf-8")
    finally:
        CONFIG.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    main()
