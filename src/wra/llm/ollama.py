import logging
from typing import Any

from ollama import Client

from .client import LLMClient

logger = logging.getLogger(__name__)


class OllamaClient(LLMClient):
    """Ollama implementation backed by the official Python client."""

    def __init__(self, base_url: str) -> None:
        self._client = Client(host=base_url)
        logger.debug("Initialized Ollama client: %s", base_url)

    def generate(
        self,
        prompt: str,
        model: str,
        options: dict[str, Any] | None = None,
        images: list[str | bytes] | None = None,
    ) -> str:
        logger.info(
            "LLM request: model=%s prompt_chars=%d images=%d options=%s",
            model,
            len(prompt),
            len(images or []),
            options or {},
        )
        try:
            response = self._client.generate(
                model=model,
                prompt=prompt,
                options=options,
                images=images,
            )
        except Exception:
            logger.exception("LLM request failed: model=%s", model)
            raise
        result = response.response
        logger.info("LLM response received: model=%s response_chars=%d", model, len(result))
        logger.debug("LLM raw response: model=%s response=%s", model, result)
        return result
