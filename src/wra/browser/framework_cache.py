from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlopen

if TYPE_CHECKING:
    from wra.core.models import FrameworkConfig

DEFAULT_CACHE_DIR = Path(".cache/frameworks")

logger = logging.getLogger(__name__)

_EXPECTED_SHA256: dict[str, str] = {}


class FrameworkIntegrityError(RuntimeError):
    """A framework asset's bytes do not match the sha256 pinned in the catalog."""


def cache_filename_for_url(url: str, extension: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{digest}{extension}"


def cached_path_for_url(url: str, extension: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / cache_filename_for_url(url, extension)


def _check_sha256(url: str, data: bytes, sha256: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256.lower():
        raise FrameworkIntegrityError(f"sha256 mismatch for framework asset {url}: expected {sha256.lower()}, got {actual}")


def download_framework_asset(url: str, dest: Path, timeout: int = 30, sha256: str | None = None) -> None:
    """Download a single catalog CDN URL into dest, creating parent dirs as needed.

    Shared by the prefetch script and the browser evaluator so the download
    call never drifts between the two. With sha256 the bytes are verified
    before anything touches the cache: a mismatch raises FrameworkIntegrityError
    and nothing is written. Without sha256 the asset is cached unverified and
    a warning is logged.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with urlopen(url, timeout=timeout) as response:
        data = response.read()
    if sha256:
        _check_sha256(url, data, sha256)
    else:
        logger.warning("framework asset has no pinned sha256, cached unverified: %s", url)
    partial.write_bytes(data)
    partial.replace(dest)


def ensure_cached_path_for_url(
    url: str, extension: str, cache_dir: Path = DEFAULT_CACHE_DIR, sha256: str | None = None
) -> Path:
    """Return the cached copy of url, downloading it first if it isn't cached yet.

    When the catalog pins a sha256 (passed in, or registered by iter_framework_urls),
    a cache hit is verified too. A cached file that no longer matches is deleted and
    downloaded again, and the fresh download is itself verified, so tampering is
    healed or, if the CDN also serves different bytes, raises FrameworkIntegrityError.

    Raises RuntimeError naming the url if the download fails, so a broken
    render is never silently evaluated as a real one.
    """
    sha256 = sha256 or _EXPECTED_SHA256.get(url)
    dest = cached_path_for_url(url, extension, cache_dir)
    if dest.is_file():
        if not sha256 or hashlib.sha256(dest.read_bytes()).hexdigest() == sha256.lower():
            return dest
        logger.warning("cached framework asset does not match its pinned sha256, discarding: %s", dest)
        dest.unlink()
    try:
        download_framework_asset(url, dest, sha256=sha256)
    except OSError as exc:
        raise RuntimeError(f"Failed to download framework asset for browser evaluation: {url} ({exc})") from exc
    return dest


def read_verified_framework_bytes(url: str, path: Path, sha256: str | None = None) -> bytes:
    """Read path once and return those bytes, checked against the pinned sha256 if there is one.

    Callers must write out the returned bytes rather than re-read path, so what
    was verified is exactly what gets used. Raises FrameworkIntegrityError on a mismatch.
    """
    sha256 = sha256 or _EXPECTED_SHA256.get(url)
    data = path.read_bytes()
    if sha256:
        _check_sha256(url, data, sha256)
    return data


def iter_framework_entries(frameworks: FrameworkConfig) -> list[tuple[str, str, str | None]]:
    """List each catalog CDN URL with its file extension and pinned sha256 (None if unpinned).

    The extension is the one the loading tag implies. Companion assets derived
    from a catalog URL (the Bootstrap bundle, GSAP ScrollTrigger) use the entry's
    companion_sha256.
    """
    entries: list[tuple[str, str, str | None]] = []
    for framework in frameworks.css:
        entries.append((framework.cdn, ".css" if framework.loading_tag("link") == "link" else ".js", framework.sha256))
        if framework.cdn.endswith("/css/bootstrap.min.css"):
            companion = framework.cdn.removesuffix("css/bootstrap.min.css") + "js/bootstrap.bundle.min.js"
            entries.append((companion, ".js", framework.companion_sha256))
    for framework in frameworks.js:
        entries.append((framework.cdn, ".css" if framework.loading_tag("script") == "link" else ".js", framework.sha256))
        if framework.cdn.endswith("/gsap.min.js"):
            companion = framework.cdn.removesuffix("gsap.min.js") + "ScrollTrigger.min.js"
            entries.append((companion, ".js", framework.companion_sha256))
    return entries


def iter_framework_urls(frameworks: FrameworkConfig) -> list[tuple[str, str]]:
    """List each catalog CDN URL with the file extension its loading tag implies.

    Shared by the prefetch script and the browser evaluator so the URL-to-filename
    mapping never drifts between the two. Also registers each pinned sha256 so
    ensure_cached_path_for_url verifies it for callers that only pass the URL.
    """
    pairs: list[tuple[str, str]] = []
    for url, extension, sha256 in iter_framework_entries(frameworks):
        pairs.append((url, extension))
        if sha256:
            _EXPECTED_SHA256[url] = sha256
        else:
            _EXPECTED_SHA256.pop(url, None)
    return pairs
