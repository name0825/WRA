from datetime import datetime
from pathlib import Path

import yaml

from wra.analyzer.analyzer import HTMLAnalyzer
from wra.core.models import WRAConfig
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
    analyzer = HTMLAnalyzer(
        llm_client=llm_client,
        analyzer_model=config.models.analyzer,
    )
    features = analyzer.analyze_file(INPUT_PATH)

    run_dir = create_run_directory()
    features_path = run_dir / "features.json"
    features_path.write_text(features.model_dump_json(indent=2), encoding="utf-8")

    logger.info("Feature Model written to %s", features_path)


if __name__ == "__main__":
    main()
