"""Reading the log directory for the admin console.

Log files run to megabytes (``llm.log`` keeps whole prompts at DEBUG), so every
read here works from the end of the file and never loads the whole thing.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LEVEL_ORDER = {name: index for index, name in enumerate(LEVELS)}
_BLOCK = 64 * 1024


@dataclass(frozen=True)
class LogFile:
    name: str
    path: Path
    size: int
    modified: datetime

    @property
    def size_text(self) -> str:
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


def list_logs(directory: Path) -> list[LogFile]:
    if not directory.is_dir():
        return []
    files = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".log":
            continue
        stat = path.stat()
        files.append(LogFile(path.name, path, stat.st_size, datetime.fromtimestamp(stat.st_mtime)))
    return sorted(files, key=lambda item: item.modified, reverse=True)


def safe_log_path(directory: Path, name: str) -> Path | None:
    """A log name names a file in the log directory, never a path into the tree."""
    # A drive-relative name such as "C:x.log" is not joined under the directory
    # on Windows - pathlib hands back the drive-relative path instead.
    if "/" in name or "\\" in name or ":" in name or name in {".", ".."} or not name.endswith(".log"):
        return None
    path = directory / name
    return path if path.is_file() else None


def read_tail(path: Path, lines: int) -> list[str]:
    """The last ``lines`` lines, read backwards a block at a time."""
    if lines <= 0 or not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        remaining = handle.tell()
        chunks: list[bytes] = []
        found = 0
        while remaining > 0 and found <= lines:
            step = min(_BLOCK, remaining)
            remaining -= step
            handle.seek(remaining)
            block = handle.read(step)
            found += block.count(b"\n")
            chunks.append(block)
    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def line_level(line: str) -> str | None:
    """The level of a log line, or none for continuation lines (tracebacks)."""
    parts = line.split(" | ", 2)
    if len(parts) < 2:
        return None
    candidate = parts[1].strip()
    return candidate if candidate in LEVEL_ORDER else None


def filter_lines(lines: list[str], min_level: str | None = None, query: str | None = None) -> list[str]:
    """Keep lines at or above ``min_level`` that contain ``query``.

    A line with no level of its own belongs to the line above it - a traceback
    body - so it is kept exactly when its owner was kept.
    """
    threshold = LEVEL_ORDER.get(min_level or "", 0)
    needle = (query or "").lower()
    kept = []
    keeping = False
    for line in lines:
        level = line_level(line)
        if level is not None:
            keeping = LEVEL_ORDER[level] >= threshold and (not needle or needle in line.lower())
        if keeping:
            kept.append(line)
    return kept
