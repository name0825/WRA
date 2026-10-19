"""Reading and rewriting the config files from the admin console.

An edit is validated the way the pipeline would load it - the edited text
merged with the other config files and run through ``WRAConfig`` - before it
replaces anything, and the previous text is kept as a ``.bak`` alongside it.
"""

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

import yaml
from pydantic import ValidationError

from wra.core.config import CONFIG_DIR, CONFIG_FILES, load_raw
from wra.core.models import InputConfig, LoggingConfig, ViewerConfig, WRAConfig

EDITABLE = CONFIG_FILES
BACKUPS_KEPT = 10

# One browser can hold the console in several tabs. Backing a file up and
# rewriting it has to be one step, or a save can back up what another already
# replaced.
_save_lock = Lock()


@dataclass(frozen=True)
class ConfigFile:
    name: str
    path: Path
    exists: bool
    modified: datetime | None


def list_config_files() -> list[ConfigFile]:
    files = []
    for name in EDITABLE:
        path = CONFIG_DIR / name
        exists = path.is_file()
        files.append(ConfigFile(name, path, exists, datetime.fromtimestamp(path.stat().st_mtime) if exists else None))
    return files


def config_path(name: str) -> Path | None:
    return CONFIG_DIR / name if name in EDITABLE else None


def read_text(name: str) -> str:
    path = config_path(name)
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def validate(name: str, text: str) -> None:
    """Raise ``ValueError`` when the edited file would not load.

    The check is the merged one on purpose: a valid YAML file can still leave
    the pipeline unable to start, and finding that out at the next run instead
    of at save time is what the console is meant to avoid.
    """
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"YAML 구문 오류: {error}") from error
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError("설정 파일의 최상위는 매핑이어야 합니다.")
    try:
        WRAConfig.model_validate(load_raw(overrides={name: parsed}))
    except ValidationError as error:
        raise ValueError(f"설정 검증 실패:\n{error}") from error
    except FileNotFoundError as error:
        raise ValueError(str(error)) from error


def prune_backups(path: Path, keep: int = BACKUPS_KEPT) -> list[Path]:
    """Drop all but the newest ``keep`` backups of one config file.

    The names carry a sortable timestamp, so the oldest are simply the first.
    Returns what was removed.
    """
    backups = sorted(path.parent.glob(f"{path.name}.*.bak"))
    dropped = backups[:-keep] if keep > 0 else backups
    for stale in dropped:
        stale.unlink(missing_ok=True)
    return dropped


# viewer.admin.run_command feeds a Popen the console itself starts, and
# viewer.debug / viewer.host control the Werkzeug debugger and which
# interface the server answers on; none of those may change from the
# console's own editor, in any config file.
_PROTECTED_VIEWER_KEYS = ("admin", "debug", "host")


def _merged_viewer_block(overrides: dict[str, dict] | None = None) -> dict:
    return load_raw(required=(), overrides=overrides).get("viewer", {})


# Directories the viewer itself serves from or writes into. ``input.directory``
# feeds the input console (which allows saving .yaml), and ``logging.directory``
# / ``viewer.output_root`` are read and written the same way; letting any of
# them resolve outside the repository, or overlap ``config/``, would let an
# edit here reach files this very check is meant to keep off limits.
_PATH_SETTINGS = (
    (InputConfig, "input", "directory", "input.directory"),
    (LoggingConfig, "logging", "directory", "logging.directory"),
    (ViewerConfig, "viewer", "output_root", "viewer.output_root"),
)


def _validate_path_settings(raw: dict) -> None:
    repo_root = CONFIG_DIR.resolve().parent
    config_dir = CONFIG_DIR.resolve()
    for model_cls, section, key, label in _PATH_SETTINGS:
        value = getattr(model_cls.model_validate(raw.get(section) or {}), key)
        # Relative to the repository root, the same base a relative CONFIG_DIR
        # resolves against, not the process's current directory: the two only
        # coincide when nothing has pointed CONFIG_DIR elsewhere.
        resolved = (repo_root / value).resolve()
        outside_repo = resolved != repo_root and repo_root not in resolved.parents
        overlaps_config = resolved == config_dir or resolved in config_dir.parents or config_dir in resolved.parents
        if outside_repo or overlaps_config:
            raise ValueError(f"{label} 는 저장소 안, config 디렉터리 밖의 경로여야 합니다: {value}")


def save(name: str, text: str) -> Path:
    """Validate, back the current file up, then write. Returns the backup path."""
    path = config_path(name)
    if path is None:
        raise ValueError(f"편집할 수 없는 파일입니다: {name}")
    with _save_lock:
        # The merged before/after comparison has to run under the same lock as
        # the write: otherwise two concurrent saves can each validate against
        # the disk state the other is about to replace, and both pass.
        validate(name, text)
        parsed = yaml.safe_load(text) or {}
        before = _merged_viewer_block()
        after_raw = load_raw(required=(), overrides={name: parsed})
        after = after_raw.get("viewer", {})
        if name == "config.yaml":
            # config.yaml is the file that legitimately owns viewer.*, so its own
            # edits to the block are allowed except for the protected keys.
            blocked = any(before.get(key) != after.get(key) for key in _PROTECTED_VIEWER_KEYS)
        else:
            # Any other file has no business changing viewer.* at all: that key
            # only reaches the merged config by overwriting what config.yaml set.
            blocked = before != after
        if blocked:
            raise ValueError("viewer 설정은 콘솔에서 바꿀 수 없습니다. 설정 파일을 직접 편집하세요.")
        _validate_path_settings(after_raw)
        # Two saves in the same second must not have the second backup overwrite
        # the first, which would drop the revision the first save replaced.
        backup = path.with_suffix(path.suffix + f".{datetime.now():%Y%m%d_%H%M%S_%f}.bak")
        if path.is_file():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text.replace("\r\n", "\n"))
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        prune_backups(path)
    return backup
