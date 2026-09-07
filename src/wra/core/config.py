"""Loader for the split configuration files.

Settings live in three files so that user-local values and static reference data
can be tracked separately: config.yaml and models.yaml are user-local copies of
their .sample, while frameworks.yaml is the tracked CDN catalog.
"""

from collections.abc import Sequence
from pathlib import Path

import yaml

from wra.core.models import WRAConfig

CONFIG_DIR = Path("config")
CONFIG_FILES = ("config.yaml", "models.yaml", "frameworks.yaml")
REQUIRED_FILES = ("config.yaml", "models.yaml")


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_raw(required: Sequence[str] = REQUIRED_FILES) -> dict:
    """Merge the top-level keys of the config files; each key lives in one file.

    ``required`` narrows what a caller cannot run without: the viewer reads only
    the ``viewer:`` block, so a checkout with no models.yaml should still take
    that block from config.yaml instead of falling back to the defaults.
    """
    data: dict = {}
    for name in CONFIG_FILES:
        path = CONFIG_DIR / name
        if not path.is_file():
            if name in required:
                raise FileNotFoundError(f"Config file not found: {path.as_posix()}. Copy {path.as_posix()}.sample to {path.as_posix()} first.")
            continue
        data.update(_read(path))
    return data


def load_config() -> WRAConfig:
    return WRAConfig.model_validate(load_raw())
