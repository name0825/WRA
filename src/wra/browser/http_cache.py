"""On-demand HTTPS cache for URLs referenced by LLM-generated HTML.

Unlike framework_cache.py (which only serves URLs from a fixed, prefetched
catalog), this fetches an allowlisted URL over HTTPS exactly once on a cache
miss and reuses the cached copy forever after. The URL comes from untrusted,
LLM-generated markup, so every step here treats it as attacker-controlled:
host allowlist, https-only, no following a redirect off the allowlist, a
response size cap, a connect/read timeout, and a cache filename derived only
from a hash of the URL (never from any attacker-controlled path segment).
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request as urllib_request
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_HTTP_CACHE_DIR = Path(".cache/http")
ALLOWED_HOSTS = frozenset({"fonts.googleapis.com", "fonts.gstatic.com"})
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 5
# A default urllib User-Agent makes Google serve legacy CSS pointing at
# whole-file .ttf fonts (which blow the per-file size cap for CJK families);
# a current desktop Chrome UA gets woff2 sliced by unicode-range instead.
CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
# Bumped whenever the request shape (e.g. User-Agent) changes what Google
# returns for the same URL, so a cache entry fetched under the old shape is
# never mistaken for one fetched under the new one.
_CACHE_KEY_VERSION = "v2"

_CONTENT_TYPE_EXTENSIONS = {
    "text/css": ".css",
    "font/woff2": ".woff2",
    "font/woff": ".woff",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "application/font-woff": ".woff",
    "application/font-woff2": ".woff2",
    "application/x-font-ttf": ".ttf",
    "application/vnd.ms-fontobject": ".eot",
}


def is_allowed_url(url: str, allowed_hosts: frozenset[str] = ALLOWED_HOSTS) -> bool:
    """True only for an https URL whose host is on the allowlist."""
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in allowed_hosts
        and port in (None, 443)
    )


class _AllowlistRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Refuses to follow a redirect off the host allowlist or past a hop cap."""

    max_redirections = MAX_REDIRECTS

    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self._allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_url(newurl, self._allowed_hosts):
            raise URLError(f"redirect to a disallowed URL rejected: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _extension_for_content_type(content_type: str) -> str:
    base = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXTENSIONS.get(base, ".bin")


def _digest_for_url(url: str) -> str:
    return hashlib.sha256(f"{_CACHE_KEY_VERSION}:{url}".encode("utf-8")).hexdigest()


def _find_cached(cache_dir: Path, digest: str) -> Path | None:
    cache_root = cache_dir.resolve()
    if not cache_dir.is_dir():
        return None
    for candidate in cache_dir.glob(f"{digest}.*"):
        if candidate.suffix == ".tmp":
            # A leftover partial write from a killed process must never be
            # mistaken for a completed, valid cache entry.
            continue
        resolved = candidate.resolve()
        if (resolved == cache_root or cache_root in resolved.parents) and resolved.is_file():
            return resolved
    return None


def fetch_cached(
    url: str,
    cache_dir: str | Path = DEFAULT_HTTP_CACHE_DIR,
    *,
    allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
) -> Path | None:
    """Return a local cached copy of ``url``, fetching it once on a cache miss.

    Returns ``None`` for anything not on the host allowlist, and on any fetch,
    size-cap, or write failure - callers must fall back to the existing abort
    behaviour rather than let this raise out of evaluation.
    """
    if not is_allowed_url(url, allowed_hosts):
        return None
    cache_dir = Path(cache_dir)
    digest = _digest_for_url(url)
    hit = _find_cached(cache_dir, digest)
    if hit is not None:
        return hit

    try:
        opener = urllib_request.build_opener(_AllowlistRedirectHandler(allowed_hosts))
        request = urllib_request.Request(url, headers={"User-Agent": CHROME_USER_AGENT})
        with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - any fetch failure must fall back, never raise
        logger.warning("HTTP cache fetch failed: url=%s error=%s", url, exc)
        return None

    if len(body) > MAX_RESPONSE_BYTES:
        logger.warning("HTTP cache fetch exceeded size cap: url=%s", url)
        return None

    cache_path = (cache_dir / f"{digest}{_extension_for_content_type(content_type)}").resolve()
    cache_root = cache_dir.resolve()
    if cache_path != cache_root and cache_root not in cache_path.parents:
        logger.warning("HTTP cache path escaped the cache dir, rejecting: url=%s", url)
        return None

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(cache_path.name + ".tmp")
        tmp_path.write_bytes(body)
        tmp_path.replace(cache_path)
    except OSError as exc:
        logger.warning("HTTP cache write failed: url=%s error=%s", url, exc)
        return None
    return cache_path
