from datetime import datetime
from pathlib import Path

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.analyzer.vision import VisionAnalyzer
from wra.browser.evaluator import BrowserEvaluator
from wra.core.config import load_config
from wra.core.inputs import discover
from wra.core.iteration import IterationEngine
from wra.core.models import WRAConfig
from wra.core.preparation import InputPreparer
from wra.creator.creator import Creator
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import RecoveryPolicy as DesignRecoveryPolicy
from wra.designer.site_plan import SitePlanner
from wra.evaluator.llm import LLMEvaluator
from wra.generator.generator import HTMLGenerator
from wra.generator.recovery import RecoveryPolicy as GeneratorRecoveryPolicy
from wra.llm.model_selector import ModelSelector
from wra.refiner.refiner import HTMLRefiner
from wra.llm.ollama import OllamaClient
from wra.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)
INPUT_DIR = Path("input")
OUTPUT_ROOT = Path("output")


def create_run_directory() -> Path:
    timestamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = OUTPUT_ROOT / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def build_vision_analyzer(config: WRAConfig, llm_client) -> VisionAnalyzer | None:
    """Build the media vision analyzer, or none when it is off or unconfigured."""
    if not config.input.vision.enabled:
        logger.info("Vision analysis disabled by config")
        return None
    if not config.models.vision:
        logger.warning("Vision analysis requested but models.vision is empty: skipping media vision")
        return None
    spec = ModelSelector.select(config.models.vision, "vision")
    logger.info("Vision model selected: %s think=%s timeout=%s", spec.name, spec.think, spec.timeout)
    return VisionAnalyzer(llm_client=llm_client, model=spec.name, config=config.input.vision, think=spec.think, timeout=spec.timeout)


def build_site_planner(config: WRAConfig, llm_client) -> SitePlanner | None:
    """Build the site restructurer, which only `madmax` multi-page runs use."""
    if config.content.freedom != "madmax" or config.input.mode == "entry":
        return None
    spec = ModelSelector.select(config.models.designer, "designer")
    logger.info("Site planner model selected: %s", spec.name)
    return SitePlanner(llm_client=llm_client, model=spec.name, max_retries=config.recovery.max_retries, think=spec.think, timeout=spec.timeout, min_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx)


def build_creator(config: WRAConfig, llm_client) -> Creator:
    """Build the stage that writes a site's content when there is none to read."""
    models = config.models.creator or config.models.designer
    spec = ModelSelector.select(models, "creator")
    logger.info("Creator model selected: %s max_pages=%d", spec.name, config.creator.max_pages)
    return Creator(llm_client=llm_client, model=spec.name, max_pages=config.creator.max_pages, max_retries=config.recovery.max_retries, think=spec.think, timeout=spec.timeout, min_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx)


def build_refiner(config: WRAConfig, llm_client) -> HTMLRefiner | None:
    """Build the stage that edits the best page toward its weakest dimensions."""
    if not config.pipeline.refine.enabled:
        return None
    spec = ModelSelector.select(config.models.refiner or config.models.generator, "refiner")
    logger.info("Refiner model selected: %s weakest=%d", spec.name, config.pipeline.refine.weakest_dimensions)
    return HTMLRefiner(llm_client=llm_client, model=spec.name, max_retries=config.recovery.max_retries, weakest=config.pipeline.refine.weakest_dimensions, think=spec.think, timeout=spec.timeout, min_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx, output_headroom_tokens=config.recovery.generator_output_headroom_tokens)


def creating_config(config: WRAConfig) -> WRAConfig:
    """Content freedom is meaningless when the content is being written here.

    Every level below `madmax` asks the pipeline to preserve a source that does
    not exist, and the stricter ones demand coverage of collections the brief
    never filled. The identity gate still applies: what the creator decided is
    what the finished page must say.
    """
    if config.content.freedom == "madmax":
        return config
    logger.info("Creating from a brief: content freedom '%s' -> 'madmax'", config.content.freedom)
    return config.model_copy(update={"content": config.content.model_copy(update={"freedom": "madmax"})})


def main() -> None:
    config = load_config()
    configure_logging(level=config.logging.level, console_level=config.logging.console_level, log_dir=config.logging.directory)
    # INPUT_DIR is the default location; config.input.directory overrides it.
    configured = Path(config.input.directory)
    input_dir = INPUT_DIR if configured == Path("input") else configured
    logger.info("WRA starting")
    logger.info("Input: %s (mode=%s)", input_dir, config.input.mode)
    logger.info("Ollama endpoint: %s", config.ollama.base_url)
    logger.info("Content freedom: %s", config.content.freedom)
    if config.models.single.enabled:
        logger.info("Single-model mode: every role runs on %s", config.models.single.name)
        if config.models.single.num_ctx is None:
            logger.info("Single-model mode: num_ctx is unpinned, so the window is still sized per prompt")
        else:
            logger.info("Single-model mode: num_ctx pinned to %d for every request", config.models.single.num_ctx)
        if config.input.vision.enabled:
            logger.warning("Single-model mode: %s also serves as the vision model, so it must be multimodal", config.models.single.name)
    if not input_dir.is_dir():
        logger.error("Input directory not found: %s", input_dir)
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    bundle = discover(input_dir, entry=config.input.entry, asset_directory=config.input.asset_directory)
    if bundle.creates:
        logger.info("No source page found: creating a site from the brief (%d chars)", len(bundle.brief))
        config = creating_config(config)

    llm_client = OllamaClient(config.ollama.base_url, keep_alive=config.ollama.keep_alive)
    design_recovery_policy = DesignRecoveryPolicy(max_retries=config.recovery.max_retries, initial_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx, growth_power=config.recovery.growth_power, output_headroom_tokens=config.recovery.designer_output_headroom_tokens)
    generator_recovery_policy = GeneratorRecoveryPolicy(max_retries=config.recovery.max_retries, initial_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx, growth_power=config.recovery.growth_power, output_headroom_tokens=config.recovery.generator_output_headroom_tokens)

    analyzer_model = ModelSelector.select(config.models.analyzer, "analyzer")
    analyzer = HTMLAnalyzer(llm_client=llm_client, analyzer_model=analyzer_model.name, analyzer_think=analyzer_model.think, analyzer_timeout=analyzer_model.timeout, semantic_max_retries=config.recovery.max_retries, min_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx)
    designer = DesignPlanner(llm_client=llm_client, designer_model="", frameworks=config.frameworks, recovery_policy=design_recovery_policy, content_freedom=config.content.freedom)
    generator = HTMLGenerator(llm_client=llm_client, generator_model="", frameworks=config.frameworks, recovery_policy=generator_recovery_policy)
    evaluator = LLMEvaluator(llm_client=llm_client, model="", min_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, ceiling_num_ctx=config.recovery.initial_ceiling_num_ctx, max_retries=config.recovery.max_retries, content_freedom=config.content.freedom, growth_power=config.recovery.growth_power)
    browser_evaluator = BrowserEvaluator(config.browser) if config.browser.enabled else None
    vision_analyzer = build_vision_analyzer(config, llm_client)

    run_dir = create_run_directory()
    logger.info("Run directory: %s", run_dir)
    preparer = InputPreparer(config=config, analyzer=analyzer, vision_analyzer=vision_analyzer, site_planner=build_site_planner(config, llm_client), creator=build_creator(config, llm_client) if bundle.creates else None)
    pages = preparer.prepare(bundle, run_dir)
    engine = IterationEngine(config=config, designer=designer, generator=generator, evaluator=evaluator, browser_evaluator=browser_evaluator, refiner=build_refiner(config, llm_client))
    results = engine.run_pages(pages)
    if not results:
        logger.error("No pages were produced to redesign: %s", input_dir)
        raise RuntimeError(f"No pages were produced from {input_dir}; nothing for the iteration loop to finalize")
    for result in results[:-1]:
        logger.info("Page finished: slug=%s final=%s score=%s", result.slug, result.final_path, f"{result.evaluation.overall_score:.1f}" if result.evaluation else "n/a")
    result = results[-1]

    logger.info("Generated HTML written to %s", result.html_path or "memory")
    logger.info("HTML validation: %s", "PASS" if result.validation.valid else "FAIL")
    if result.validation.errors:
        for error in result.validation.errors:
            logger.error("HTML validation error: %s", error)
    for warning in result.validation.warnings:
        logger.warning("HTML validation warning: %s", warning)
    if result.browser.enabled:
        logger.info("Browser evaluation completed for %d viewport(s)", len(result.browser.viewports))
    if result.evaluation is not None:
        logger.info("Evaluation score: %.1f", result.evaluation.overall_score)
        logger.info("Evaluation summary: %s", result.evaluation.summary)
    logger.info("Validation result written to %s", result.validation_path or "memory")
    logger.info("Final HTML written to %s", result.final_path)
    logger.info("WRA finished")


if __name__ == "__main__":
    main()
