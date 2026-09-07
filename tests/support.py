"""Shared config fixture for the tests.

Settings are split across three files, and the sample model pools ship empty,
so a test that actually selects a model has to supply one.
"""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
SAMPLE_FILES = ("config.yaml.sample", "models.yaml.sample", "frameworks.yaml")

# The model pools the sample carried before they were emptied out. Tests that
# stub the LLM never reach these names; the CLI test really does call them.
SAMPLE_MODELS = {
    "analyzer": [{"name": "gemma4:26b"}],
    "designer": [{"name": "gemma4:26b"}],
    "generator": [{"name": "gemma4:12b"}],
    "refiner": [{"name": "gemma4:26b"}],
    "evaluator": [{"name": "gemma4:26b", "think": False, "timeout": 600}],
    "vision": [{"name": "gemma4:26b"}],
}


def sample_config_data() -> dict:
    """Raw config data merged from the sample files, with the model pools filled."""
    data: dict = {}
    for name in SAMPLE_FILES:
        data.update(yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8")) or {})
    data["models"] = {**data.get("models", {}), **SAMPLE_MODELS}
    return data
