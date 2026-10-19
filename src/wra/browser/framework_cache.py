from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlopen

if TYPE_CHECKING:
    from wra.core.models import FrameworkConfig

DEFAULT_CACHE_DIR = Path(".cache/frameworks")


def cache_filename_for_url(url: str, extension: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{digest}{extension}"


def cached_path_for_url(url: str, extension: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / cache_filename_for_url(url, extension)


def download_framework_asset(url: str, dest: Path, timeout: int = 30) -> None:
    """Download a single catalog CDN URL into dest, creating parent dirs as needed.

    Shared by the prefetch script and the browser evaluator so the download
    call never drifts between the two.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with urlopen(url, timeout=timeout) as response:
        partial.write_bytes(response.read())
    partial.replace(dest)


def ensure_cached_path_for_url(url: str, extension: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    """Return the cached copy of url, downloading it first if it isn't cached yet.

    Raises RuntimeError naming the url if the download fails, so a broken
    render is never silently evaluated as a real one.
    """
    dest = cached_path_for_url(url, extension, cache_dir)
    if dest.is_file():
        return dest
    try:
        download_framework_asset(url, dest)
    except OSError as exc:
        raise RuntimeError(f"Failed to download framework asset for browser evaluation: {url} ({exc})") from exc
    return dest


def iter_framework_urls(frameworks: FrameworkConfig) -> list[tuple[str, str]]:
    """List each catalog CDN URL with the file extension its loading tag implies.

    Shared by the prefetch script and the browser evaluator so the URL-to-filename
    mapping never drifts between the two.
    """
    pairs: list[tuple[str, str]] = []
    for framework in frameworks.css:
        pairs.append((framework.cdn, ".css" if framework.loading_tag("link") == "link" else ".js"))
        if framework.cdn.endswith("/css/bootstrap.min.css"):
            pairs.append((framework.cdn.removesuffix("css/bootstrap.min.css") + "js/bootstrap.bundle.min.js", ".js"))
    for framework in frameworks.js:
        pairs.append((framework.cdn, ".css" if framework.loading_tag("script") == "link" else ".js"))
        if framework.cdn.endswith("/gsap.min.js"):
            pairs.append((framework.cdn.removesuffix("gsap.min.js") + "ScrollTrigger.min.js", ".js"))
    return pairs
