import base64
import logging
import struct
import zlib
from collections.abc import Iterable

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 2.0
_PROBE_TIMEOUT = 60.0
_PROBE_COLD_LOAD_TIMEOUT = 180.0
_MIN_IMAGE_TOKENS = 8
_probed: set[tuple[str, str]] = set()
_OLLAMA_SUFFIX = "/ollama"


def _root_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    return stripped[: -len(_OLLAMA_SUFFIX)] if stripped.endswith(_OLLAMA_SUFFIX) else stripped


def _fetch_v1_model_capabilities(base_url: str) -> tuple[dict[str, list | None] | None, bool]:
    url = f"{_root_url(base_url)}/v1/models"
    try:
        response = httpx.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return {entry["id"]: entry.get("capabilities") for entry in data["data"]}, False
    except httpx.TransportError as exc:
        logger.debug("Host unreachable at %s for vision capability check: %s", url, exc)
        return None, True
    except Exception as exc:
        logger.debug("Could not read %s for vision capability check: %s", url, exc)
        return None, False


def _fetch_api_show_capabilities(base_url: str, model: str) -> list | None:
    url = f"{base_url.rstrip('/')}/api/show"
    try:
        response = httpx.post(url, json={"model": model}, timeout=_TIMEOUT)
        response.raise_for_status()
        capabilities = response.json().get("capabilities")
        return list(capabilities) if capabilities is not None else None
    except Exception as exc:
        logger.debug("Could not read %s for model %s vision capability check: %s", url, model, exc)
        return None


def _solid_png_base64(size: int = 224, rgb: tuple[int, int, int] = (200, 40, 40)) -> str:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * size
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


def _prompt_eval_count(base_url: str, model: str, images: list[str] | None, keep_alive: str | float | None, timeout: float) -> int:
    payload = {
        "model": model,
        "prompt": "Describe the image.",
        "stream": False,
        "options": {"num_predict": 1},
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if images:
        payload["images"] = images
    response = httpx.post(f"{base_url.rstrip('/')}/api/generate", json=payload, timeout=timeout)
    response.raise_for_status()
    return int(response.json()["prompt_eval_count"])


def _probe_image_support(base_url: str, role: str, model: str, keep_alive: str | float | None) -> None:
    key = (base_url.rstrip("/"), model)
    if key in _probed:
        return
    _probed.add(key)
    try:
        text_only = _prompt_eval_count(base_url, model, None, keep_alive, _PROBE_COLD_LOAD_TIMEOUT)
        with_image = _prompt_eval_count(base_url, model, [_solid_png_base64()], keep_alive, _PROBE_TIMEOUT)
    except Exception as exc:
        logger.debug("Image-input probe failed for model '%s' at %s: %s", model, base_url, exc)
        return
    if with_image - text_only < _MIN_IMAGE_TOKENS:
        logger.warning(
            "The server at %s ignores image inputs for model '%s' (prompt tokens %d with image vs %d without); "
            "role '%s' vision stages run blind.",
            base_url,
            model,
            with_image,
            text_only,
            role,
        )
        return
    logger.info("Image-input probe for role '%s' model '%s': prompt tokens %d with image vs %d without", role, model, with_image, text_only)


def warn_if_vision_missing(base_url: str, role: str, model_names: Iterable[str], keep_alive: str | float | None = None) -> None:
    names = sorted({name.strip() for name in model_names if name and name.strip()})
    if not names:
        return
    v1_models, host_unreachable = _fetch_v1_model_capabilities(base_url)
    if host_unreachable:
        logger.debug("Skipping per-model /api/show fallback for role '%s': host unreachable", role)
    for name in names:
        capabilities = v1_models.get(name) if v1_models is not None and name in v1_models else None
        if capabilities is None and not host_unreachable:
            capabilities = _fetch_api_show_capabilities(base_url, name)
        if capabilities is None:
            logger.debug("Vision capability unknown for role '%s' model '%s'", role, name)
            if not host_unreachable:
                _probe_image_support(base_url, role, name, keep_alive)
            continue
        if "vision" not in capabilities:
            logger.warning(
                "Role '%s' is configured with model '%s', which does not report vision capability; "
                "screenshot-based judgments (e.g. visual inspection) will be unreliable.",
                role,
                name,
            )
        elif not host_unreachable:
            _probe_image_support(base_url, role, name, keep_alive)
