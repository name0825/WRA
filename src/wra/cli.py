from datetime import datetime
from pathlib import Path

import yaml

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.core.models import WRAConfig
from wra.core.pipeline import WRAPipeline
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import RecoveryPolicy
from wra.generator.generator import HTMLGenerator
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
    recovery_policy = RecoveryPolicy(
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
        recovery_policy=recovery_policy,
    )
    generator = HTMLGenerator(
        llm_client=llm_client,
        generator_model=config.models.generator,
        frameworks=config.frameworks,
    )

    run_dir = create_run_directory()
    pipeline = WRAPipeline(
        config=config,
        analyzer=analyzer,
        designer=designer,
        generator=generator,
    )
    result = pipeline.run(INPUT_PATH, run_dir)

    logger.info("Feature Model written to %s", result.features_path)
    logger.info("DesignSpec written to %s", result.design_spec_path)
    logger.info("Generated HTML written to %s", result.html_path)
    logger.info("Final HTML written to %s", run_dir / "final.html")


if __name__ == "__main__":
    main()
