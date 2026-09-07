import logging
from typing import Any

from ollama import Client

from .client import LLMClient
from .schema import format_label

logger = logging.getLogger(__name__)

# A model with no configured timeout would otherwise inherit the ollama client's
# own default of None, which httpx reads as "wait forever": one stalled request
# then hangs a multi-hour iteration run with nothing to time it out.
DEFAULT_TIMEOUT = 900.0


class OllamaClient(LLMClient):
    """Ollama implementation backed by the official Python client."""

    def __init__(self, base_url: str, default_timeout: float = DEFAULT_TIMEOUT, keep_alive: str | float | None = None) -> None:
        self._base_url = base_url
        self._default_timeout = default_timeout
        self._keep_alive = keep_alive
        self._clients: dict[float, Client] = {}
        logger.debug("Initialized Ollama client: %s timeout=%s keep_alive=%s", base_url, default_timeout, keep_alive)

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
        # Must stay top-level: api.Options has no `format` field, so `format`
        # placed inside `options` is dropped by the server and JSON mode never
        # engages. That is why no caller passed it successfully until now.
        #
        # Pass a JSON *schema* here, never the bare string "json". Measured
        # against gemma4:26b on a 20k-char designer prompt at num_ctx=16384:
        #   no format ......... 67.8s, 5,394 chars, stop, does not validate
        #   "json" ............ ~148s, 64,315 chars, done_reason=length, runaway
        #   DesignSpec schema .. 13.7s, 4,393 chars, stop, validates
        # The bare "json" constrains the grammar without constraining the shape,
        # so a model satisfies it either by collapsing to a minimal object or by
        # padding valid JSON forever. A schema bounds the output. Every JSON
        # stage now passes its role's pydantic schema; the HTML stages
        # (generator, refiner) correctly pass nothing.
        format: str | dict | None = None,
        timeout: float | None = None,
    ) -> str:
        logger.info(
            "LLM request: model=%s prompt_chars=%d images=%d think=%s format=%s timeout=%s keep_alive=%s options=%s",
            model,
            len(prompt),
            len(images or []),
            think,
            format_label(format),
            self._resolve_timeout(timeout),
            self._keep_alive,
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
            if format is not None:
                kwargs["format"] = format
            if self._keep_alive is not None:
                kwargs["keep_alive"] = self._keep_alive
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
