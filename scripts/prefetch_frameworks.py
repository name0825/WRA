"""Download the CDN framework catalog into a local cache for offline browser evaluation.

Run on demand: python scripts/prefetch_frameworks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from wra.browser.framework_cache import DEFAULT_CACHE_DIR, cached_path_for_url, ensure_cached_path_for_url, iter_framework_entries
from wra.core.models import FrameworkConfig

FRAMEWORKS_YAML = Path(__file__).resolve().parent.parent / "config" / "frameworks.yaml"


def main() -> None:
    data = yaml.safe_load(FRAMEWORKS_YAML.read_text(encoding="utf-8")) or {}
    frameworks = FrameworkConfig.model_validate(data.get("frameworks", {}))
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url, extension, sha256 in iter_framework_entries(frameworks):
        dest = cached_path_for_url(url, extension, DEFAULT_CACHE_DIR)
        already = dest.is_file()
        try:
            ensure_cached_path_for_url(url, extension, DEFAULT_CACHE_DIR, sha256)
        except RuntimeError as exc:
            print(f"failed: {url} ({exc})")
            continue
        print(f"cached already: {url}" if already else f"downloaded: {url} -> {dest}")


if __name__ == "__main__":
    main()
