"""Download the CDN framework catalog into a local cache for offline browser evaluation.

Run on demand: python scripts/prefetch_frameworks.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from wra.browser.framework_cache import DEFAULT_CACHE_DIR, cached_path_for_url, download_framework_asset, iter_framework_urls
from wra.core.models import FrameworkConfig

FRAMEWORKS_YAML = Path(__file__).resolve().parent.parent / "config" / "frameworks.yaml"


def main() -> None:
    data = yaml.safe_load(FRAMEWORKS_YAML.read_text(encoding="utf-8")) or {}
    frameworks = FrameworkConfig.model_validate(data.get("frameworks", {}))
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url, extension in iter_framework_urls(frameworks):
        dest = cached_path_for_url(url, extension, DEFAULT_CACHE_DIR)
        if dest.is_file():
            print(f"cached already: {url}")
            continue
        try:
            download_framework_asset(url, dest)
            print(f"downloaded: {url} -> {dest}")
        except URLError as exc:
            print(f"failed: {url} ({exc})")


if __name__ == "__main__":
    main()
