from datetime import datetime
from pathlib import Path

import yaml

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.core.iteration import IterationEngine
from wra.core.models import WRAConfig
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import RecoveryPolicy as DesignRecoveryPolicy
from wra.generator.generator import HTMLGenerator
from wra.generator.recovery import RecoveryPolicy as GeneratorRecoveryPolicy
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
        raise FileNotFoundError(
            f"Config file not found: {CONFIG_PATH}. "
            "Copy config/config.yaml.sample to config/config.yaml first."
        )

    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return WRAConfig.model_validate(data)


def main() -> None:
    configure_logging()

    if not INPUT_PATH.is_file():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    config = load_config()
    llm_client = OllamaClient(config.ollama.base_url)
    design_recovery_policy = DesignRecoveryPolicy(
        max_retries=config.recovery.max_retries,
        initial_num_ctx=config.recovery.initial_num_ctx,
        max_num_ctx=config.recovery.max_num_ctx,
        context_multiplier=config.recovery.context_multiplier,
    )
    generator_recovery_policy = GeneratorRecoveryPolicy(
        max_retries=config.recovery.max_retries,
        initial_num_ctx=config.recovery.initial_num_ctx,
        max_num_ctx=config.recovery.max_num_ctx,
        context_multiplier=config.recovery.context_multiplier,
    )

    analyzer = HTMLAnalyzer(
        llm_client=llm_client,
        analyzer_model=config.models.analyzer,
    )
    designer = DesignPlanner(
        llm_client=llm_client,
        designer_model=config.models.designer,
        recovery_policy=design_recovery_policy,
    )
    generator = HTMLGenerator(
        llm_client=llm_client,
        generator_model=config.models.generator,
        frameworks=config.frameworks,
        recovery_policy=generator_recovery_policy,
    )

    run_dir = create_run_directory()
    engine = IterationEngine(
        config=config,
        analyzer=analyzer,
        designer=designer,
        generator=generator,
    )
    result = engine.run(INPUT_PATH, run_dir)

    logger.info("Generated HTML written to %s", result.html_path or "memory")
    logger.info("HTML validation: %s", "PASS" if result.validation.valid else "FAIL")
    if result.validation.errors:
        for error in result.validation.errors:
            logger.error("HTML validation error: %s", error)
    for warning in result.validation.warnings:
        logger.warning("HTML validation warning: %s", warning)
    logger.info("Validation result written to %s", result.validation_path or "memory")
    logger.info("Final HTML written to %s", result.final_path)


if __name__ == "__main__":
    main()
