"""Reading and rewriting the input directory from the admin console.

The input directory is the source site the pipeline redesigns, so the console
treats it as a plain file tree: list it, look at one file, drop new ones in,
remove the ones that no longer belong. Every path that arrives from the browser
goes through :func:`safe_path`, which resolves it and refuses anything that
does not land inside the root.
"""

import glob
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_FILES_LISTED = 2000
MAX_UPLOAD_FILES = 500
MAX_INPUT_TOTAL_BYTES = 1024 * 1024 * 1024
BACKUPS_KEPT = 5
# Kept under a dotted name so neither the listing nor the pipeline, which both
# skip entries starting with a dot, ever sees a backup as source material.
BACKUP_DIR = ".backups"

TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".xml", ".yaml", ".yml", ".csv", ".svg"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp", ".svg"}
FONT_SUFFIXES = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES | FONT_SUFFIXES


@dataclass(frozen=True)
class InputFile:
    relative: str
    path: Path
    size: int
    modified: datetime

    @property
    def kind(self) -> str:
        # Text wins over image so an SVG is shown as its source: served inline
        # it would run its own script in the console's own origin.
        suffix = self.path.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            return "text"
        if suffix in IMAGE_SUFFIXES:
            return "image"
        return "binary"

    @property
    def directory(self) -> str:
        parent = PurePosixPath(self.relative).parent
        return "" if parent == PurePosixPath(".") else parent.as_posix()

    @property
    def size_text(self) -> str:
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


def list_files(root: Path) -> list[InputFile]:
    """Every file under ``root``, hidden entries aside, sorted by path.

    A source site can hold thousands of assets, and this runs on every load of
    the dashboard and the input page, so the walk itself stops at the cap
    instead of enumerating the whole tree and truncating afterwards. Which
    files the cap keeps therefore follows the walk, not the sort.
    """
    if not root.is_dir():
        return []
    files: list[InputFile] = []
    for parent, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if not name.startswith("."))
        for name in sorted(names):
            if name.startswith("."):
                continue
            path = Path(parent) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append(InputFile(path.relative_to(root).as_posix(), path, stat.st_size,
                datetime.fromtimestamp(stat.st_mtime)))
            if len(files) >= MAX_FILES_LISTED:
                return sorted(files, key=lambda item: item.relative)
    return sorted(files, key=lambda item: item.relative)


def directory_size(root: Path) -> int:
    """Total bytes under ``root``, hidden entries included."""
    total = 0
    for parent, _directories, names in os.walk(root):
        for name in names:
            try:
                total += (Path(parent) / name).stat().st_size
            except OSError:
                continue
    return total


def safe_path(root: Path, relative: str) -> Path | None:
    """Resolve a browser-supplied relative path inside ``root``, or refuse it.

    Refusing is the point: a name may not climb out with ``..``, may not be
    absolute, and may not carry a Windows drive letter, which would otherwise
    make the join drive-relative instead of rooted at ``root``.
    """
    if not relative:
        return None
    candidate = relative.replace("\\", "/")
    if candidate.startswith("/") or ":" in candidate:
        return None
    parts = [part for part in candidate.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    resolved = (root / Path(*parts)).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    return resolved


def safe_file(root: Path, relative: str) -> Path | None:
    path = safe_path(root, relative)
    return path if path is not None and path.is_file() else None


def clean_upload_path(name: str) -> str | None:
    """The relative path an uploaded file should take under the input root.

    Browsers hand back either a bare name or, for a dropped folder, a path with
    separators. Each segment is trimmed to what a file name may hold; a segment
    that survives as empty, ``.`` or ``..`` makes the whole path unusable.
    """
    candidate = (name or "").replace("\\", "/")
    parts = []
    for part in candidate.split("/"):
        part = part.strip().strip(".")
        if not part:
            continue
        part = "".join(character for character in part if character not in '<>:"|?*' and ord(character) >= 32)
        if not part or part in (".", ".."):
            return None
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


def read_text(path: Path, limit: int = MAX_TEXT_BYTES) -> tuple[str, bool]:
    """The file's text and whether it was cut short at ``limit`` bytes."""
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def save_upload(root: Path, relative: str, data: bytes) -> Path:
    """Write one uploaded file under ``root``. Raises ``ValueError`` on refusal."""
    cleaned = clean_upload_path(relative)
    if cleaned is None:
        raise ValueError(f"쓸 수 없는 파일 이름입니다: {relative}")
    suffix = PurePosixPath(cleaned).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"허용하지 않는 확장자입니다: {cleaned}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"파일이 너무 큽니다 ({cleaned}, 최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)")
    path = safe_path(root, cleaned)
    if path is None:
        raise ValueError(f"입력 폴더 밖을 가리킵니다: {cleaned}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def write_text(root: Path, relative: str, text: str) -> Path:
    """Replace one text file's content, keeping the old content as a backup.

    Returns the backup path. Raises ``ValueError`` when the file is not one the
    console may edit.
    """
    path = safe_file(root, relative)
    if path is None:
        raise ValueError(f"입력 파일이 없습니다: {relative}")
    item = describe(root, path)
    if item.kind != "text":
        raise ValueError(f"텍스트 파일이 아닙니다: {item.relative}")
    if item.size > MAX_TEXT_BYTES:
        # The editor only ever held the first MAX_TEXT_BYTES of it, so saving
        # what it holds would drop the rest.
        raise ValueError(f"파일이 커서 편집할 수 없습니다 (최대 {MAX_TEXT_BYTES // 1024}KB)")
    body = text.replace("\r\n", "\n")
    if len(body.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError(f"편집할 수 있는 크기를 넘었습니다 (최대 {MAX_TEXT_BYTES // 1024}KB)")
    backup = _backup(root, path, item.relative)
    path.write_text(body, encoding="utf-8", newline="\n")
    return backup


def _backup(root: Path, path: Path, relative: str) -> Path:
    directory = root.resolve() / BACKUP_DIR / PurePosixPath(relative).parent
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / f"{path.name}.{datetime.now():%Y%m%d_%H%M%S_%f}.bak"
    backup.write_bytes(path.read_bytes())
    # A source file name may hold glob metacharacters, which would make the
    # pattern miss its own backups and match someone else's.
    stale = sorted(directory.glob(f"{glob.escape(path.name)}.*.bak"))[:-BACKUPS_KEPT]
    for old in stale:
        old.unlink(missing_ok=True)
    return backup


def delete(root: Path, relative: str) -> bool:
    """Remove one file, then any directories its removal left empty."""
    path = safe_file(root, relative)
    if path is None:
        return False
    path.unlink()
    root_resolved = root.resolve()
    parent = path.parent
    # Best-effort: a concurrent upload landing between the emptiness check and
    # the rmdir, or a transient lock, must not turn this delete into a failure.
    try:
        while parent != root_resolved and root_resolved in parent.parents:
            if any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent
    except OSError:
        pass
    return True


def describe(root: Path, path: Path) -> InputFile:
    """One already-resolved file, in the shape the listing uses."""
    stat = path.stat()
    return InputFile(path.resolve().relative_to(root.resolve()).as_posix(), path, stat.st_size,
        datetime.fromtimestamp(stat.st_mtime))
