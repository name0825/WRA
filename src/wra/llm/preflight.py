import logging
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from wra.core.models import WRAConfig
from wra.llm.ollama import describe_exception, redact_url

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
_LATEST = ":latest"
_EXO_SUFFIX = "/ollama"


def _variants(name: str) -> set[str]:
    return {name[: -len(_LATEST)], name} if name.endswith(_LATEST) else {name, name + _LATEST}


def _served_names(base_url: str) -> set[str] | None:
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        response = httpx.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        names: set[str] = set()
        for entry in response.json()["models"]:
            for key in ("name", "model"):
                if isinstance(entry.get(key), str):
                    names.add(entry[key])
        return names
    except Exception as exc:
        logger.warning("Could not list models at %s; skipping the model availability check: %s", redact_url(url), describe_exception(exc))
        return None


def _exo_root(base_url: str) -> str | None:
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if not path.endswith(_EXO_SUFFIX):
        return None
    return urlunsplit((parts.scheme, parts.netloc, path[: -len(_EXO_SUFFIX)], "", ""))


def _placed_ids(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "modelId" and isinstance(value, str):
                found.add(value)
            else:
                found |= _placed_ids(value)
    elif isinstance(node, list):
        for value in node:
            found |= _placed_ids(value)
    return found


def _placed_names(root: str) -> set[str] | None:
    url = f"{root.rstrip('/')}/state"
    try:
        response = httpx.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        instances = response.json()["instances"]
        if not isinstance(instances, dict):
            raise ValueError("unexpected instances shape")
        placed = _placed_ids(instances)
        if instances and not placed:
            raise ValueError("no model ids among the instances")
        return placed
    except Exception as exc:
        logger.warning("Could not read placed instances at %s; skipping the placement check: %s", redact_url(url), describe_exception(exc))
        return None


def configured_model_names(config: WRAConfig, *, creates: bool) -> list[str]:
    models = config.models
    pools = [models.analyzer, models.designer, models.generator, models.evaluator]
    if config.pipeline.refine.enabled:
        pools.append(models.refiner or models.generator)
    if creates:
        pools.append(models.creator or models.designer)
    if config.input.vision.enabled:
        pools.append(models.vision)
    return list(dict.fromkeys(spec.name for pool in pools for spec in pool))


def ensure_models_available(base_url: str, model_names: Iterable[str]) -> None:
    served = _served_names(base_url)
    if served is None:
        return
    missing = [name for name in dict.fromkeys(model_names) if not _variants(name) & served]
    if missing:
        raise RuntimeError(f"Model(s) not found on {redact_url(base_url)}: {', '.join(missing)}")
    root = _exo_root(base_url)
    if root is not None:
        placed = _placed_names(root)
        if placed is not None:
            unplaced = [name for name in dict.fromkeys(model_names) if not _variants(name) & placed]
            if unplaced:
                raise RuntimeError(f"Model(s) downloaded but not placed on {redact_url(base_url)}: {', '.join(unplaced)}")
    logger.info("Model availability check passed at %s", redact_url(base_url))


def ensure_browser_available() -> None:
    message = "Playwright browser not installed; run: playwright install chromium"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            installed = Path(playwright.chromium.executable_path).exists()
    except Exception as exc:
        logger.debug("Playwright check failed: %s", describe_exception(exc))
        raise RuntimeError(message) from exc
    if not installed:
        raise RuntimeError(message)
