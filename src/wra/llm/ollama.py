import json
import logging
import threading
from time import monotonic, sleep
from typing import Any

from ollama import Client, ResponseError

from .byte_fallback import strip_byte_fallback_markers
from .client import LLMClient
from .schema import format_label

logger = logging.getLogger(__name__)

# A model with no configured timeout would otherwise inherit the ollama client's
# own default of None, which httpx reads as "wait forever": one stalled request
# then hangs a multi-hour iteration run with nothing to time it out.
DEFAULT_TIMEOUT = 900.0

_TRANSPORT_RETRIES = 3
_RECOVERY_WAIT_CAP = 600.0
_PROBE_INTERVAL = 10.0
_PROBE_TIMEOUT = 30.0


class LLMTransportError(RuntimeError):
    pass


def _transport_kind(exc: Exception) -> str | None:
    if isinstance(exc, json.JSONDecodeError) and not exc.doc.strip():
        return "empty_body"
    if isinstance(exc, ResponseError) and exc.status_code == 404 and "No instance found" in str(exc):
        return "no_instance"
    return None


class OllamaClient(LLMClient):
    """Ollama implementation backed by the official Python client."""

    def __init__(
        self,
        base_url: str,
        default_timeout: float = DEFAULT_TIMEOUT,
        keep_alive: str | float | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        self._base_url = base_url
        self._default_timeout = default_timeout
        self._keep_alive = keep_alive
        self._default_options: dict[str, Any] = {}
        if temperature is not None:
            self._default_options["temperature"] = temperature
        self._seed = seed
        self._seed_counter = 0
        self._seed_lock = threading.Lock()
        self._clients: dict[float, Client] = {}
        self._responsive_models: set[str] = set()
        logger.debug("Initialized Ollama client: %s timeout=%s keep_alive=%s temperature=%s seed=%s", base_url, default_timeout, keep_alive, temperature, seed)

    def _resolve_timeout(self, timeout: float | None) -> float:
        return self._default_timeout if timeout is None else timeout

    def _get_client(self, timeout: float | None) -> Client:
        resolved = self._resolve_timeout(timeout)
        if resolved not in self._clients:
            self._clients[resolved] = Client(host=self._base_url, timeout=resolved)
        return self._clients[resolved]

    def _wait_for_recovery(self, model: str, deadline: float) -> int | None:
        probe_kwargs: dict[str, Any] = {
            "model": model,
            "prompt": "Reply with the single word: pong",
            "options": {"num_predict": 8},
        }
        if self._keep_alive is not None:
            probe_kwargs["keep_alive"] = self._keep_alive
        failed_probes = 0
        while monotonic() < deadline:
            try:
                self._get_client(_PROBE_TIMEOUT).generate(**probe_kwargs)
            except Exception:
                failed_probes += 1
                sleep(max(0.0, min(_PROBE_INTERVAL, deadline - monotonic())))
            else:
                self._responsive_models.add(model)
                return failed_probes
        return None

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
        merged_options = {**self._default_options, **(options or {})}
        if self._seed is not None and "seed" not in merged_options:
            # A fixed seed replays a byte-identical response for a repeated prompt.
            with self._seed_lock:
                merged_options["seed"] = self._seed + self._seed_counter
                self._seed_counter += 1
        logger.info(
            "LLM request: model=%s prompt_chars=%d images=%d think=%s format=%s timeout=%s keep_alive=%s options=%s",
            model,
            len(prompt),
            len(images or []),
            think,
            format_label(format),
            self._resolve_timeout(timeout),
            self._keep_alive,
            merged_options,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "options": merged_options,
            "images": images,
        }
        if think is not None:
            kwargs["think"] = think
        if format is not None:
            kwargs["format"] = format
        if self._keep_alive is not None:
            kwargs["keep_alive"] = self._keep_alive
        resends = 0
        waited = 0.0
        while True:
            try:
                response = self._get_client(timeout).generate(**kwargs)
                self._responsive_models.add(model)
                break
            except Exception as exc:
                kind = _transport_kind(exc)
                if isinstance(exc, json.JSONDecodeError) and kind is None:
                    logger.warning("LLM returned a non-empty invalid body: model=%s body_chars=%d body=%r", model, len(exc.doc), exc.doc[:200])
                if kind is None or model not in self._responsive_models:
                    logger.exception("LLM request failed: model=%s", model)
                    raise
                if resends >= _TRANSPORT_RETRIES:
                    logger.exception("LLM request failed: model=%s", model)
                    raise LLMTransportError(f"LLM transport failure ({kind}) for model {model} after {resends + 1} attempt(s)") from exc
                logger.warning(
                    "LLM transport failure: model=%s kind=%s attempt=%d/%d; waiting for the model to answer",
                    model,
                    kind,
                    resends + 1,
                    _TRANSPORT_RETRIES,
                )
                started = monotonic()
                failed_probes = self._wait_for_recovery(model, started + _RECOVERY_WAIT_CAP - waited)
                elapsed = monotonic() - started
                waited += elapsed
                if failed_probes is None:
                    self._responsive_models.discard(model)
                    logger.error("LLM transport recovery timed out after %.0fs: model=%s", waited, model)
                    raise LLMTransportError(f"LLM transport failure ({kind}) for model {model} after {resends + 1} attempt(s)") from exc
                logger.info("LLM transport recovery: model=%s ready after %.1fs (%d failed probe(s))", model, elapsed, failed_probes)
                resends += 1

        result, marker_count = strip_byte_fallback_markers(response.response)
        if marker_count:
            logger.warning(
                "LLM response contained %d byte-fallback marker(s): model=%s",
                marker_count,
                model,
            )
        done_reason = getattr(response, "done_reason", None)
        prompt_tokens = getattr(response, "prompt_eval_count", None)
        eval_tokens = getattr(response, "eval_count", None)
        logger.info(
            "LLM response received: model=%s response_chars=%d done=%s done_reason=%s prompt_tokens=%s eval_tokens=%s",
            model,
            len(result),
            getattr(response, "done", None),
            done_reason,
            prompt_tokens,
            eval_tokens,
        )
        num_ctx = merged_options.get("num_ctx")
        eval_count = eval_tokens if isinstance(eval_tokens, int) else 0
        if isinstance(prompt_tokens, int) and isinstance(num_ctx, int) and prompt_tokens + eval_count > num_ctx:
            logger.warning("LLM context window exceeded: model=%s prompt_tokens=%d eval_tokens=%d num_ctx=%d", model, prompt_tokens, eval_count, num_ctx)
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
