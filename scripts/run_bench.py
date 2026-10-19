"""Run the quality benchmark: every sample through `redesign` with benchmark config.

config/config.yaml is swapped per sample and always restored from a backup.
Usage: python scripts/run_bench.py <log_dir> [sample ...]
"""
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


def bench_config(text: str, input_dir: Path) -> str:
    for pattern, value in OVERRIDES:
        value = value or f'"{input_dir.as_posix()}"'
        text, count = re.subn(pattern, lambda m: m.group(1) + value, text, count=1)
        if count != 1:
            raise SystemExit(f"config key not found: {pattern}")
    return re.sub(r"(\n  refine:\n    enabled: )\S+", r"\1false", text, count=1)


def copied_input(log_dir: Path, sample: str) -> Path:
    target = log_dir / f"{sample}_input"
    target.mkdir(parents=True, exist_ok=True)
    for name in COPIED_INPUTS[sample]:
        source = next(p for p in (ROOT / "input" / f"_{name}", ROOT / "input" / name) if p.exists())
        shutil.copy(source, target / name)
    return target


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


def main() -> None:
    log_dir = Path(sys.argv[1]).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    names = sys.argv[2:] or list(SAMPLES)
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
            CONFIG.write_text(bench_config(original, input_dir), encoding="utf-8")
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
