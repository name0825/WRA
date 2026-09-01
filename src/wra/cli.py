from pathlib import Path
from typing import Any

import yaml

from .core.models import ModelConfig, OllamaConfig, PipelineConfig, WRAConfig
from .llm.ollama import OllamaClient
from .utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

CONFIG_PATH = Path("config/config.yaml")
SAMPLE_CONFIG_PATH = Path("config/config.yaml.sample")


def load_config(path: Path = CONFIG_PATH) -> WRAConfig:
    if not path.is_file():
        raise FileNotFoundError(
            f"Configuration file not found: {path}. "
            f"Copy {SAMPLE_CONFIG_PATH} to {path} before running WRA."
        )

    with path.open("r", encoding="utf-8") as file:
        raw: dict[str, Any] = yaml.safe_load(file) or {}

    ollama = raw.get("ollama", {})
    models = raw.get("models", {})
    pipeline = raw.get("pipeline", {})

    return WRAConfig(
        ollama=OllamaConfig(base_url=ollama.get("base_url", "http://localhost:11434")),
        models=ModelConfig(
            analyzer=models.get("analyzer", ""),
            designer=models.get("designer", ""),
            generator=models.get("generator", ""),
            evaluator=models.get("evaluator", ""),
        ),
        pipeline=PipelineConfig(
            max_iterations=pipeline.get("max_iterations", 3),
            evaluation_threshold=pipeline.get("evaluation_threshold", 8.0),
        ),
    )


def main() -> None:
    configure_logging()
    config = load_config()

    if not config.models.analyzer:
        raise ValueError("No analyzer model configured in config/config.yaml.")

    client = OllamaClient(config.ollama.base_url)
    response = client.generate(
        prompt="Respond with exactly: WRA Ollama connection OK",
        model=config.models.analyzer,
    )
    logger.info("Ollama response: %s", response)


if __name__ == "__main__":
    main()
