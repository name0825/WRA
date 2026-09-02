from datetime import datetime
from pathlib import Path

import yaml

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.browser.evaluator import BrowserEvaluator
from wra.core.iteration import IterationEngine
from wra.core.models import WRAConfig
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import RecoveryPolicy as DesignRecoveryPolicy
from wra.evaluator.llm import LLMEvaluator
from wra.generator.generator import HTMLGenerator
from wra.generator.recovery import RecoveryPolicy as GeneratorRecoveryPolicy
from wra.llm.model_selector import ModelSelector
from wra.llm.ollama import OllamaClient
from wra.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)
INPUT_PATH = Path("input/input.html")
OUTPUT_ROOT = Path("output")
CONFIG_PATH = Path("config/config.yaml")


def create_run_directory() -> Path:
    timestamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = OUTPUT_ROOT / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def load_config() -> WRAConfig:
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}. Copy config/config.yaml.sample to config/config.yaml first.")
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return WRAConfig.model_validate(data)


def main() -> None:
    config = load_config()
    configure_logging(level=config.logging.level, console_level=config.logging.console_level, log_dir=config.logging.directory)
    logger.info("WRA starting")
    logger.info("Input: %s", INPUT_PATH)
    logger.info("Ollama endpoint: %s", config.ollama.base_url)
    if not INPUT_PATH.is_file():
        logger.error("Input file not found: %s", INPUT_PATH)
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    llm_client = OllamaClient(config.ollama.base_url)
    design_recovery_policy = DesignRecoveryPolicy(max_retries=config.recovery.max_retries, initial_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, context_multiplier=config.recovery.context_multiplier)
    generator_recovery_policy = GeneratorRecoveryPolicy(max_retries=config.recovery.max_retries, initial_num_ctx=config.recovery.initial_num_ctx, max_num_ctx=config.recovery.max_num_ctx, context_multiplier=config.recovery.context_multiplier)

    analyzer_model = ModelSelector.select(config.models.analyzer, "analyzer")
    analyzer = HTMLAnalyzer(llm_client=llm_client, analyzer_model=analyzer_model.name, analyzer_think=analyzer_model.think, analyzer_timeout=analyzer_model.timeout, semantic_max_retries=config.recovery.max_retries)
    designer = DesignPlanner(llm_client=llm_client, designer_model="", frameworks=config.frameworks, recovery_policy=design_recovery_policy)
    generator = HTMLGenerator(llm_client=llm_client, generator_model="", frameworks=config.frameworks, recovery_policy=generator_recovery_policy)
    evaluator = LLMEvaluator(llm_client=llm_client, model="")
    browser_evaluator = BrowserEvaluator(config.browser) if config.browser.enabled else None

    run_dir = create_run_directory()
    logger.info("Run directory: %s", run_dir)
    engine = IterationEngine(config=config, analyzer=analyzer, designer=designer, generator=generator, evaluator=evaluator, browser_evaluator=browser_evaluator)
    result = engine.run(INPUT_PATH, run_dir)

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
