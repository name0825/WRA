import json
import logging
import re
from pathlib import Path

from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
from wra.llm.client import LLMClient
from wra.llm.ollama import LLMTransportError
from wra.llm.response import normalize_json_response
from wra.llm.schema import bound_strings
from wra.validator.visual import find_render_collapse_defects

logger = logging.getLogger(__name__)

VISUAL_INSPECTION_PREFIX = "Visual inspection:"
DEFECT_KINDS = ("unreadable_text", "overlapping_elements", "clipped_text", "empty_gap", "broken_image", "raw_markup", "broken_layout")
CAPPED_DEFECT_KINDS = ("unreadable_text", "overlapping_elements", "broken_image", "raw_markup")
MAX_FINDINGS = 8
_DESCRIPTION_CHARS = 200

VISUAL_CHECK_SCHEMA = bound_strings({
    "type": "object",
    "properties": {
        "defects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "viewport": {"type": "string"},
                    "kind": {"type": "string", "enum": list(DEFECT_KINDS)},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "low"]},
                },
                "required": ["viewport", "kind", "description", "severity"],
            },
        },
    },
    "required": ["defects"],
})

# Kinds an LLM visual read commonly hallucinates without a matching layout or
# contrast defect (see plan_114a); dropped unless the claimed viewport has deterministic
# DOM evidence for it.
DOM_CHECKED_KINDS = ("overlapping_elements", "broken_layout", "clipped_text", "unreadable_text")
_OCCLUSION_WORDING = re.compile(r"\b(?:covered|obscured|overlapped|hidden) by\b|\bhidden behind\b", re.IGNORECASE)
_BOTTOM_EDGE_WORDING = re.compile(r"\b(?:cut(?:s)? off|cut|clipped|truncated|cropped)\b(?:\s+\w+){0,2}?\s+(?:at|by|below|beyond|past)\s+(?:the\s+)?(?:bottom(?: edge)? of the (?:viewport|screen|window)\b|viewport(?:'s)? bottom(?: edge)?\b|fold\b)", re.IGNORECASE)


def _matching_viewports(viewport_hint: str, viewports: list[BrowserViewportResult]) -> list[BrowserViewportResult]:
    """Viewports the claim's free-text `viewport` field (e.g. `mobile`, `390px`, `tablet (768px)`) refers to, or all of them if none match."""
    text = viewport_hint.lower()
    matched = [viewport for viewport in viewports if viewport.name.lower() in text or f"{viewport.width}px" in text]
    return matched or list(viewports)


def _has_dom_evidence(kind: str, viewport_hint: str, browser_result: BrowserEvaluationResult, html: str) -> bool:
    if kind not in DOM_CHECKED_KINDS:
        return True
    matched = _matching_viewports(viewport_hint, browser_result.viewports)
    if kind == "overlapping_elements":
        return any(viewport.occluded_text_elements or viewport.header_overlap_pairs for viewport in matched)
    if kind == "clipped_text":
        return any(viewport.overflow_elements or viewport.edge_hugging_text_elements for viewport in matched)
    if kind == "unreadable_text":
        return any(viewport.contrast_failures for viewport in matched)
    collapse_findings = find_render_collapse_defects(html, browser_result)
    for viewport in matched:
        if viewport.horizontal_overflow or viewport.overflow_elements or viewport.wrapped_text_elements:
            return True
        marker = f"{viewport.name} {viewport.width}px:"
        if any(marker in finding or "px:" not in finding for finding in collapse_findings):
            return True
    return False


class VisualInspector:
    """Ask the evaluator model about the rendered screenshots alone.

    Given the HTML, the evaluator scores the code it reads instead of the screen
    it is shown. This call sees only the screenshots and a narrow defect list.
    """

    def __init__(self, llm_client: LLMClient, prompt_path: str | Path = "prompts/evaluator/visual_check.md", num_ctx: int = 32768) -> None:
        self._llm_client = llm_client
        self._num_ctx = num_ctx
        self._system_prompt = Path(prompt_path).read_text(encoding="utf-8")

    def inspect(self, browser_result: BrowserEvaluationResult | None, html: str, model: str, think: bool | None = False, timeout: float | None = None) -> list[str]:
        """High-severity defects seen in the screenshots; [] when there are none or the call fails.

        A claim in `DOM_CHECKED_KINDS` (an overlap, a broken layout, clipped text, or unreadable text) with no
        matching deterministic DOM evidence is dropped rather than kept: 3/3
        checked claims of this kind were false (see plan_114a).
        """
        if browser_result is None or not browser_result.enabled or not model:
            return []
        shots = [viewport for viewport in browser_result.viewports if viewport.screenshot_path]
        if not shots:
            return []
        listing = "\n".join(f"- Image {index}: `{viewport.name}` viewport, {viewport.width}px wide" for index, viewport in enumerate(shots, 1))
        prompt = f"{self._system_prompt}\n\n## Screenshots\n\n{listing}"
        logger.info("Visual inspection started: model=%s screenshots=%d", model, len(shots))
        try:
            for attempt in (1, 2):
                response = self._llm_client.generate(prompt=prompt, model=model, options={"num_ctx": self._num_ctx}, images=[viewport.screenshot_path for viewport in shots], think=think, format=VISUAL_CHECK_SCHEMA, timeout=timeout)
                try:
                    defects = json.loads(normalize_json_response(response))["defects"]
                    break
                except json.JSONDecodeError as exc:
                    if attempt == 2:
                        raise
                    logger.warning("Visual inspection reply was not JSON, retrying once: %s reply=%r", exc, response[:200])
            findings: list[str] = []
            dropped: list[str] = []
            for defect in defects:
                if not isinstance(defect, dict) or defect.get("severity") != "high" or defect.get("kind") not in DEFECT_KINDS:
                    continue
                description = " ".join(str(defect.get("description") or "").split())[:_DESCRIPTION_CHARS]
                if not description:
                    continue
                kind = defect["kind"]
                viewport_hint = str(defect.get("viewport") or "")
                finding = f"{VISUAL_INSPECTION_PREFIX} [{viewport_hint}] {kind}: {description}"
                checked_kind = "overlapping_elements" if kind == "clipped_text" and _OCCLUSION_WORDING.search(description) else kind
                if (kind == "clipped_text" and not _OCCLUSION_WORDING.search(description) and _BOTTOM_EDGE_WORDING.search(description)) or not _has_dom_evidence(checked_kind, viewport_hint, browser_result, html):
                    dropped.append(finding)
                    continue
                if finding not in findings:
                    findings.append(finding)
        except LLMTransportError:
            raise
        except Exception as exc:
            logger.warning("Visual inspection failed, continuing without it: %s", exc)
            logger.error("Visual inspection did not run: model=%s screenshots=%d error=%s", model, len(shots), exc)
            return []
        findings = findings[:MAX_FINDINGS]
        if dropped:
            logger.info("Visual inspection claims dropped (no DOM evidence): %s", dropped)
        if findings:
            logger.warning("Visual inspection defects: %s", findings)
        return findings
