import logging
from typing import Any

from ollama import Client

from .client import LLMClient

logger = logging.getLogger(__name__)

# A model with no configured timeout would otherwise inherit the ollama client's
# own default of None, which httpx reads as "wait forever": one stalled request
# then hangs a multi-hour iteration run with nothing to time it out.
DEFAULT_TIMEOUT = 900.0


class OllamaClient(LLMClient):
    """Ollama implementation backed by the official Python client."""

    def __init__(self, base_url: str, default_timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url
        self._default_timeout = default_timeout
        self._clients: dict[float, Client] = {}
        logger.debug("Initialized Ollama client: %s timeout=%s", base_url, default_timeout)

    def _resolve_timeout(self, timeout: float | None) -> float:
        return self._default_timeout if timeout is None else timeout

    def _get_client(self, timeout: float | None) -> Client:
        resolved = self._resolve_timeout(timeout)
        if resolved not in self._clients:
            self._clients[resolved] = Client(host=self._base_url, timeout=resolved)
        return self._clients[resolved]

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
            self._resolve_timeout(timeout),
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
        done_reason = getattr(response, "done_reason", None)
        logger.info(
            "LLM response received: model=%s response_chars=%d done=%s done_reason=%s",
            model,
            len(result),
            getattr(response, "done", None),
            done_reason,
        )
        logger.debug("LLM raw response: model=%s response=%s", model, result)

        # Ollama explicitly reports `done_reason=length` when generation stops
        # because the available generation/context budget was exhausted. Keep
        # this signal instead of returning a partial JSON response that the
        # planner may incorrectly classify as ordinary malformed JSON.
        if done_reason == "length":
            raise RuntimeError(
                "LLM generation stopped due to output token limit "
                "(Ollama done_reason=length)"
            )

        return result
