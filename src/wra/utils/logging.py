import logging
from pathlib import Path

_LOGGERS = (
    "analyzer",
    "designer",
    "generator",
    "validator",
    "evaluator",
    "browser",
    "pipeline",
    "llm",
    "output",
)


def configure_logging(
    level: str | int = "INFO",
    log_dir: str | Path = "log",
    console_level: str | int = "INFO",
) -> None:
    """Configure console logging and one log file per WRA component."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setLevel(_resolve_level(console_level))
    console.setFormatter(formatter)
    root.addHandler(console)

    file_level = _resolve_level(level)
    for component in _LOGGERS:
        handler = logging.FileHandler(log_path / f"{component}.log", encoding="utf-8")
        handler.setLevel(file_level)
        handler.setFormatter(formatter)
        handler.addFilter(_ComponentFilter(component))
        root.addHandler(handler)

    all_handler = logging.FileHandler(log_path / "wra.log", encoding="utf-8")
    all_handler.setLevel(file_level)
    all_handler.setFormatter(formatter)
    all_handler.addFilter(_WRAFilter())
    root.addHandler(all_handler)


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = getattr(logging, str(level).upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"Invalid log level: {level}")
    return resolved


class _ComponentFilter(logging.Filter):
    def __init__(self, component: str) -> None:
        super().__init__()
        self._prefix = f"wra.{component}"

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == self._prefix or record.name.startswith(f"{self._prefix}.")


class _WRAFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "wra" or record.name.startswith("wra.")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
