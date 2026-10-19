"""평가기 변별력 확인. reference-polished 원본과 망가뜨린 변형을 같은 조건으로 채점한다.

사용법: discrimination.py [반복 수] [--legacy-ref GIT_REF]
--legacy-ref를 주면 그 ref의 evaluator/llm.py와 프롬프트로 만든 평가기도 같은 FeatureModel·DesignSpec·순서로 채점한다.
"""
import argparse
import importlib.util
import json
import random
import re
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.browser.evaluator import BrowserEvaluator
from wra.cli import _resolve_seed, build_vision_analyzer
from wra.core.config import load_config
from wra.core.inputs import discover
from wra.core.models import EvaluationResult
from wra.core.preparation import InputPreparer
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import RecoveryPolicy as DesignRecoveryPolicy
from wra.evaluator.llm import LLMEvaluator
from wra.llm.model_selector import ModelSelector
from wra.llm.ollama import OllamaClient
from wra.utils.logging import configure_logging
from wra.validator.anchors import find_broken_anchors
from wra.validator.tokens import find_corrupted_tokens
from wra.validator.visual import find_visual_defects

SRC = Path("benchmarks/reference-polished")
SECTION_IDS = ["story", "features", "specs", "editions", "faq", "store"]


def inline(html: str) -> str:
    css = (SRC / "style.css").read_text(encoding="utf-8")
    js = (SRC / "script.js").read_text(encoding="utf-8")
    html, css_count = re.subn(r'<link rel="stylesheet" href="(?:assets/)?style\.css">', lambda _: f"<style>\n{css}\n</style>", html)
    html, js_count = re.subn(r'<script src="(?:assets/)?script\.js" defer></script>', lambda _: f"<script>\n{js}\n</script>", html)
    if (css_count, js_count) != (1, 1):
        raise SystemExit(f"inline failed: css={css_count} js={js_count}")
    return html


def variants(html: str) -> dict[str, str]:
    broken = html
    for sid in SECTION_IDS:
        broken = broken.replace(f' id="{sid}"', f' id="{sid}-x"')
    result = {
        "original": html,
        "broken_anchors": broken,
        "no_images": re.sub(r"<img\b[^>]*>", "", html),
        "no_css": re.sub(r"<style>.*?</style>", "", html, flags=re.S),
    }
    for name, variant in result.items():
        if name != "original" and variant == html:
            raise SystemExit(f"variant {name} is identical to original")
    return result


def load_legacy_evaluator(ref: str, workdir: Path):
    workdir.mkdir(parents=True, exist_ok=True)
    files = {}
    for rel in ("src/wra/evaluator/llm.py", "prompts/evaluator/llm.md"):
        target = workdir / Path(rel).name
        target.write_bytes(subprocess.run(["git", "show", f"{ref}:{rel}"], check=True, capture_output=True).stdout)
        files[rel] = target
    spec = importlib.util.spec_from_file_location("wra_legacy_evaluator", files["src/wra/evaluator/llm.py"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LLMEvaluator, files["prompts/evaluator/llm.md"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repeats", nargs="?", type=int, default=2)
    parser.add_argument("--legacy-ref")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    config = load_config()
    config = config.model_copy(update={"input": config.input.model_copy(update={"directory": str(SRC)})})
    configure_logging(level=config.logging.level, console_level=config.logging.console_level, log_dir=config.logging.directory)
    client = OllamaClient(config.ollama.base_url, keep_alive=config.ollama.keep_alive, temperature=config.ollama.temperature, seed=_resolve_seed(config.ollama.seed))
    rec = config.recovery
    am = ModelSelector.select(config.models.analyzer, "analyzer")
    analyzer = HTMLAnalyzer(llm_client=client, analyzer_model=am.name, analyzer_think=am.think, analyzer_timeout=am.timeout, semantic_max_retries=rec.max_retries, min_num_ctx=rec.initial_num_ctx, max_num_ctx=rec.max_num_ctx, ceiling_num_ctx=rec.initial_ceiling_num_ctx)
    run_dir = Path("output") / datetime.now().strftime("discrim_%Y%m%d_%H%M%S")
    bundle = discover(SRC, entry=config.input.entry, asset_directory=config.input.asset_directory)
    page = InputPreparer(config=config, analyzer=analyzer, vision_analyzer=build_vision_analyzer(config, client), site_planner=None).prepare(bundle, run_dir)[0]

    dm = ModelSelector.select(config.models.designer, "designer")
    designer = DesignPlanner(llm_client=client, designer_model="", frameworks=config.frameworks, recovery_policy=DesignRecoveryPolicy(max_retries=rec.max_retries, initial_num_ctx=rec.initial_num_ctx, max_num_ctx=rec.max_num_ctx, ceiling_num_ctx=rec.initial_ceiling_num_ctx, growth_power=rec.growth_power, output_headroom_tokens=rec.designer_output_headroom_tokens), content_freedom=config.content.freedom)
    spec = designer.plan(page.features, model=dm.name, think=dm.think, timeout=dm.timeout)

    em = ModelSelector.select(config.models.evaluator, "evaluator")
    common = dict(llm_client=client, model="", min_num_ctx=rec.initial_num_ctx, max_num_ctx=rec.max_num_ctx, ceiling_num_ctx=rec.initial_ceiling_num_ctx, max_retries=rec.max_retries, content_freedom=config.content.freedom, growth_power=rec.growth_power)
    evaluators = {"current": LLMEvaluator(**common)}
    if args.legacy_ref:
        legacy_cls, legacy_prompt = load_legacy_evaluator(args.legacy_ref, run_dir / "_legacy")
        evaluators["legacy"] = legacy_cls(prompt_path=legacy_prompt, **common)
    browser = BrowserEvaluator(config.browser)

    htmls = variants(inline(page.html))
    for name, html in htmls.items():
        (run_dir / f"{name}.html").write_text(html, encoding="utf-8")
    jobs = [(ev, name, rep) for ev in evaluators for name in htmls for rep in range(args.repeats)]
    random.Random(args.seed).shuffle(jobs)

    results = []
    for index, (ev_name, name, rep) in enumerate(jobs):
        html = htmls[name]
        b = browser.evaluate(html=html, output_dir=run_dir / "_browser" / f"s{index:03d}", iteration=1, asset_root=run_dir)
        extra = {"visual_findings": find_visual_defects(html, page.features, b)} if ev_name == "current" else {}
        ev = evaluators[ev_name].evaluate(page.features, spec, html, browser_result=b, content_findings=find_corrupted_tokens(html, page.features), ux_findings=find_broken_anchors(html), model=em.name, think=em.think, timeout=em.timeout, **extra)
        row = {"evaluator": ev_name, "variant": name, "rep": rep, "slot": index, "overall": ev.overall_score, **{k: getattr(ev, k).score for k in EvaluationResult.model_fields if hasattr(getattr(ev, k), "score")}}
        results.append({"row": row, "evaluation": ev.model_dump(mode="json")})
        print(json.dumps(row, ensure_ascii=False), flush=True)

    summary = {}
    for ev_name in evaluators:
        base = [r["row"]["overall"] for r in results if r["row"]["evaluator"] == ev_name and r["row"]["variant"] == "original"]
        for name in htmls:
            scores = [r["row"]["overall"] for r in results if r["row"]["evaluator"] == ev_name and r["row"]["variant"] == name]
            summary[f"{ev_name}/{name}"] = {"scores": scores, "mean": statistics.mean(scores), "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0, "delta_vs_original": statistics.mean(scores) - statistics.mean(base)}
    meta = {"evaluator_model": em.name, "designer_model": dm.name, "repeats": args.repeats, "legacy_ref": args.legacy_ref, "seed": args.seed}
    (run_dir / "discrimination.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=2))
    print("RUN_DIR", run_dir)


if __name__ == "__main__":
    main()
