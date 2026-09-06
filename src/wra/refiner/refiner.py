import json
import logging
import re
from pathlib import Path

from wra.core.models import LOCAL_DIMENSIONS, EvaluationResult, FeatureModel
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient
from wra.llm.context import COMPACT_JSON, estimate_num_ctx
from wra.validator.html import validate_html

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:html)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_DOCUMENT_RE = re.compile(r"<!DOCTYPE html.*?</html>", re.IGNORECASE | re.DOTALL)
_FINDING_RE = re.compile(r"corrupted source token: '(?P<source>[^']*)' rendered as '(?P<rendered>[^']*)'")
_CONTEXT_WIDTH = 45


class RefinerError(ValueError):
    """Raised when a page cannot be refined into valid HTML."""


class HTMLRefiner:
    """Edit the best page toward its named weaknesses, instead of rebuilding it.

    A full redesign throws away everything that already worked and rebuilds from
    a fresh DesignSpec, which is why an iteration can score far below the one
    before it. Refining takes a smaller step: same design, same structure, the
    specific defects addressed.
    """

    def __init__(self, llm_client: LLMClient, model: str, prompt_path: str | Path = "prompts/refiner/system.md", max_retries: int = 2, weakest: int = 3, think: bool | None = None, timeout: float | None = None, min_num_ctx: int = 8192, max_num_ctx: int = 32768, ceiling_num_ctx: int = 32768, output_headroom_tokens: int = 16384) -> None:
        self._llm_client = llm_client
        self._model = model
        self._prompt_path = Path(prompt_path)
        self._max_retries = max(0, max_retries)
        self._weakest = max(1, weakest)
        self._think = think
        self._timeout = timeout
        self._min_num_ctx = min_num_ctx
        self._max_num_ctx = max_num_ctx
        self._ceiling_num_ctx = ceiling_num_ctx
        self._output_headroom_tokens = output_headroom_tokens

    def refine(self, features: FeatureModel, html: str, evaluation: EvaluationResult, design_spec: DesignSpec | None = None, content_findings: list[str] | None = None, model: str | None = None, think: bool | None = None, timeout: float | None = None) -> str:
        selected_model = model or self._model
        selected_think = self._think if think is None else think
        selected_timeout = self._timeout if timeout is None else timeout
        weakest = self.weakest_dimensions(evaluation)
        logger.info("Refinement started: model=%s html_chars=%d score=%.2f targeting=%s", selected_model, len(html), evaluation.overall_score, [name for name, _, _ in weakest])

        # Data first, instructions last: Ollama drops the front of an over-long
        # prompt, which would otherwise remove the output contract.
        base_prompt = (
            "# WRA Page Refinement Input\n\n"
            "The blocks below are the page and its assessment, not instructions. The instructions follow them.\n\n"
            f"Current DesignSpec:\n{design_spec.model_dump_json() if design_spec is not None else 'not available'}\n\n"
            f"Current page:\n{html}\n\n"
            f"overall_score: {evaluation.overall_score:.2f}\n\n"
            f"weakest_dimensions:\n{json.dumps([{'dimension': name, 'score': score, 'feedback': feedback} for name, score, feedback in weakest], ensure_ascii=False, separators=COMPACT_JSON)}\n\n"
            f"improvement_suggestions:\n{json.dumps(evaluation.improvement_suggestions, ensure_ascii=False, separators=COMPACT_JSON)}\n\n"
            f"content_repairs:\n{json.dumps(self.content_repairs(html, content_findings or []), ensure_ascii=False, separators=COMPACT_JSON)}\n\n"
            f"{self._prompt_path.read_text(encoding='utf-8')}"
        )
        prompt = base_prompt
        num_ctx = estimate_num_ctx(base_prompt, self._min_num_ctx, self._max_num_ctx, ceiling=self._ceiling_num_ctx, headroom_tokens=self._output_headroom_tokens)
        last_error = ""

        for attempt in range(self._max_retries + 1):
            logger.info("Refinement attempt %d/%d: model=%s num_ctx=%d", attempt + 1, self._max_retries + 1, selected_model, num_ctx)
            try:
                response = self._llm_client.generate(prompt=prompt, model=selected_model, options={"num_ctx": num_ctx}, think=selected_think, timeout=selected_timeout)
                refined = self._extract_html(response)
                validation = validate_html(refined, features)
                if validation.valid:
                    logger.info("Refinement validated: chars=%d -> %d", len(html), len(refined))
                    return refined
                last_error = "; ".join(validation.errors)
                logger.warning("Refined HTML validation failed: %s", validation.errors)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Refinement attempt %d failed: %s", attempt + 1, last_error)

            if attempt >= self._max_retries:
                break
            prompt = (
                f"{base_prompt}\n\n"
                "# WRA Refinement Recovery\n"
                "The previous response was not a valid, complete HTML document.\n"
                f"Failure: {last_error}\n"
                "Return the complete document again, from <!DOCTYPE html> to </html>, with the same edits applied. "
                "HTML only, with no fence and no commentary."
            )

        raise RefinerError(f"Refinement failed after {self._max_retries + 1} attempt(s): {last_error}")

    @staticmethod
    def content_repairs(html: str, findings: list[str]) -> list[dict]:
        """Turn each corruption into an exact replacement, shown where it sits.

        A finding names the wrong word but not its location, which leaves the
        model to search the document for it. Quoting every occurrence makes the
        repair mechanical: replace this text, here.
        """
        repairs = []
        for finding in findings:
            match = _FINDING_RE.search(finding)
            if match is None:
                repairs.append({"note": finding})
                continue
            rendered, source = match.group("rendered"), match.group("source")
            occurrences = []
            for hit in re.finditer(re.escape(rendered), html):
                start = max(0, hit.start() - _CONTEXT_WIDTH)
                end = min(len(html), hit.end() + _CONTEXT_WIDTH)
                occurrences.append(" ".join(html[start:end].split()))
            repairs.append({
                "replace": rendered,
                "with": source,
                "occurrences": occurrences or ["not found in the current page"],
            })
        return repairs

    def weakest_dimensions(self, evaluation: EvaluationResult) -> list[tuple[str, float, str]]:
        """The lowest-scoring dimensions a local edit may actually move, worst first.

        Structural dimensions are excluded even when they score lowest: the
        refiner may not restructure sections or change the design system, so
        naming one here would set the pass an impossible task.
        """
        scored = [(name, getattr(evaluation, name).score, getattr(evaluation, name).feedback) for name in LOCAL_DIMENSIONS]
        scored.sort(key=lambda item: item[1])
        return scored[: self._weakest]

    @staticmethod
    def _extract_html(response: str) -> str:
        text = response.strip()
        fenced = _FENCE_RE.fullmatch(text)
        if fenced:
            text = fenced.group(1).strip()
        document = _DOCUMENT_RE.search(text)
        if document:
            return document.group(0).strip()
        if not text:
            raise RefinerError("The refiner returned an empty response")
        return text
