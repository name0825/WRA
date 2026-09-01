import logging
from typing import Any

from ollama import Client

from .client import LLMClient

logger = logging.getLogger(__name__)


class OllamaClient(LLMClient):
    """Ollama implementation backed by the official Python client."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._clients: dict[float | None, Client] = {None: Client(host=base_url)}
        logger.debug("Initialized Ollama client: %s", base_url)

    def _get_client(self, timeout: float | None) -> Client:
        if timeout not in self._clients:
            self._clients[timeout] = Client(host=self._base_url, timeout=timeout)
        return self._clients[timeout]

    def generate(
        self,
        prompt: str,
        model: str,
        options: dict[str, Any] | None = None,
        images: list[str | bytes] | None = None,
        *,
        think: bool | None = None,
        timeout: float | None = None,
    ) -> str:
        logger.info(
            "LLM request: model=%s prompt_chars=%d images=%d think=%s timeout=%s options=%s",
            model,
            len(prompt),
            len(images or []),
            think,
            timeout,
            options or {},
        )
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "options": options,
                "images": images,
            }
            if think is not None:
                kwargs["think"] = think
            response = self._get_client(timeout).generate(**kwargs)
        except Exception:
            logger.exception("LLM request failed: model=%s", model)
            raise
        result = response.response
        logger.info("LLM response received: model=%s response_chars=%d", model, len(result))
        logger.debug("LLM raw response: model=%s response=%s", model, result)
        return result
