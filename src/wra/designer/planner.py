import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from wra.core.models import ContentFreedom, EvaluationResult, FeatureModel, FrameworkConfig, coverage_is_enforced
from wra.designer.recovery import (
    DesignRecoveryError,
    RecoveryPolicy,
    RecoveryStrategy,
    build_recovery_prompt,
    classify_exception,
    strategy_for,
)
from wra.designer.schema import DesignSpec
from wra.llm.client import LLMClient
from wra.llm.context import COMPACT_JSON, estimate_num_ctx
from wra.llm.response import json_root_text, normalize_json_response
from wra.validator.design import _resolve_path, required_coverage_paths, validate_design_spec
from wra.llm.schema import bound_strings

logger = logging.getLogger(__name__)

# Upper bounds, measured over the 73 DesignSpecs this pipeline has produced:
# sections ran 3-6 and components per section 1-7. Twice the observed maximum
# cannot bind a normal plan, and it is the only thing that ends a runaway --
# a lower bound alone leaves the model free to keep adding sections until the
# context is exhausted, which is what it does when recovery asks for more.
_MAX_SECTIONS = 12
_MAX_COMPONENTS = 12

# A content_source ending in a single bracket index, e.g. `content.paragraphs[25]`.
# Range/slice syntax like `[2..4]` has no all-digit contents and does not match.
_TRAILING_BRACKET_INDEX_RE = re.compile(r"^(?P<base>.+)\[(?P<idx>\d+)\]$")

# A content_source into a table row, e.g. `content.tables[4][1]`: table index,
# then row index. This is the one shape the benchmark showed failing on every
# retry attempt, so it is fixed immediately instead of waiting for the retry
# budget to exhaust.
_TABLE_SOURCE_RE = re.compile(r"^content\.tables\[(?P<table>\d+)\]\[(?P<row>\d+)\]$")


def _design_spec_schema() -> dict:
    """DesignSpec's schema, narrowed to what `validate_design_spec` demands.

    Pydantic derives no constraint from a list field, so the plain schema admits
    a spec with no sections and sections with no components -- the exact shapes
    the validator rejects. The model then meets the schema, fails validation,
    and enters recovery holding an instruction to add more; under a grammar that
    lets a valid object grow without bound it answers by generating until the
    context runs out. Stating the requirement in the grammar keeps that response
    unreachable instead of recoverable.
    """
    schema = DesignSpec.model_json_schema()
    schema["required"] = [*schema["required"], "sections"]
    schema["properties"]["sections"].update(minItems=1, maxItems=_MAX_SECTIONS)
    section = schema["$defs"]["SectionSpec"]
    section["required"] = [*section["required"], "components"]
    section["properties"]["components"].update(minItems=1, maxItems=_MAX_COMPONENTS)
    return bound_strings(schema)


DESIGN_SPEC_SCHEMA = _design_spec_schema()


class DesignPlanner:
    """Create and revise structured design plans from a generic web FeatureModel."""

    def __init__(self, llm_client: LLMClient, designer_model: str, frameworks: FrameworkConfig | None = None, prompt_path: str | Path = "prompts/designer/system.md", revision_prompt_path: str | Path = "prompts/designer/revision.md", recovery_policy: RecoveryPolicy | None = None, designer_think: bool | None = None, designer_timeout: float | None = None, content_freedom: ContentFreedom = "low") -> None:
        self._llm_client = llm_client
        self._designer_model = designer_model
        self._content_freedom = content_freedom
        self._designer_think = designer_think
        self._designer_timeout = designer_timeout
        self._frameworks = frameworks or FrameworkConfig()
        self._prompt_path = Path(prompt_path)
        self._revision_prompt_path = Path(revision_prompt_path)
        self._recovery_policy = recovery_policy or RecoveryPolicy()

    def plan(self, features: FeatureModel, design_constraints: dict[str, Any] | None = None, generation_rules: list[str] | None = None, model: str | None = None, think: bool | None = None, timeout: float | None = None) -> DesignSpec:
        selected_model = model or self._designer_model
        selected_think = self._designer_think if think is None else think
        selected_timeout = self._designer_timeout if timeout is None else timeout
        logger.info("Design planning started: model=%s mode=%s", selected_model, (design_constraints or {}).get("mode", "unspecified"))
        prompt_template = self._prompt_path.read_text(encoding="utf-8")
        original_prompt = self._build_plan_prompt(prompt_template, features, design_constraints, generation_rules, self._frameworks, self._content_freedom)
        return self._generate_and_validate(
            original_prompt=original_prompt,
            features=features,
            recovery_context=self._build_recovery_context(features, design_constraints, self._frameworks, self._content_freedom),
            model=selected_model,
            think=selected_think,
            timeout=selected_timeout,
        )

    def revise(self, features: FeatureModel, previous_design_spec: DesignSpec, previous_html: str, evaluation: EvaluationResult, design_constraints: dict[str, Any] | None = None, model: str | None = None, think: bool | None = None, timeout: float | None = None) -> DesignSpec:
        selected_model = model or self._designer_model
        selected_think = self._designer_think if think is None else think
        selected_timeout = self._designer_timeout if timeout is None else timeout
        logger.info("Design revision started: model=%s previous_score=%.2f", selected_model, evaluation.overall_score)

        system_template = self._prompt_path.read_text(encoding="utf-8")
        revision_template = self._revision_prompt_path.read_text(encoding="utf-8")
        prompt = self._build_revision_prompt(
            system_template,
            revision_template,
            features,
            previous_design_spec,
            previous_html,
            evaluation,
            design_constraints,
            self._frameworks,
            self._content_freedom,
        )
        return self._generate_and_validate(
            original_prompt=prompt,
            features=features,
            recovery_context=self._build_recovery_context(features, design_constraints, self._frameworks, self._content_freedom),
            model=selected_model,
            think=selected_think,
            timeout=selected_timeout,
        )

    def _generate_and_validate(self, original_prompt, features, recovery_context, model, think=None, timeout=None):
        base_num_ctx = estimate_num_ctx(original_prompt, self._recovery_policy.initial_num_ctx, self._recovery_policy.max_num_ctx, ceiling=self._recovery_policy.ceiling_num_ctx, headroom_tokens=self._recovery_policy.output_headroom_tokens)
        current_num_ctx = base_num_ctx
        previous_response = None
        prompt = original_prompt
        last_error = DesignRecoveryError.UNKNOWN
        last_details: list[str] = []
        last_spec: DesignSpec | None = None

        for retry in range(self._recovery_policy.max_retries + 1):
            attempt = retry + 1
            logger.info("Design Planner attempt %d/%d: model=%s num_ctx=%d think=%s timeout=%s", attempt, self._recovery_policy.max_retries + 1, model, current_num_ctx, think, timeout)
            options = {"num_ctx": current_num_ctx}
            try:
                response = self._llm_client.generate(prompt=prompt, model=model, options=options, think=think, format=DESIGN_SPEC_SCHEMA, timeout=timeout)
                previous_response = response
                logger.debug("Designer response received: chars=%d", len(response))
                spec = self._parse_response(response)
                logger.debug("DesignSpec JSON parsed successfully")
                last_spec = spec
                validation = validate_design_spec(spec, features, self._frameworks, self._content_freedom)
                if not validation.valid:
                    last_error = DesignRecoveryError.INVALID_DESIGN
                    last_details = validation.errors
                    logger.warning("DesignSpec validation failed: errors=%s", validation.errors)
                    if all(self._is_table_source_error(error) for error in validation.errors):
                        coerced_spec = self._coerce_table_sources(copy.deepcopy(spec), features)
                        if coerced_spec is not None:
                            revalidation = validate_design_spec(coerced_spec, features, self._frameworks, self._content_freedom)
                            if revalidation.valid:
                                logger.info("DesignSpec validation passed after coercing content.tables indexes on the same attempt")
                                return coerced_spec
                    strategy = strategy_for(last_error)
                else:
                    logger.info("DesignSpec validation passed: sections=%d", len(spec.sections))
                    return spec
            except ValueError as exc:
                last_error = self._classify_parse_error(exc)
                last_details = [str(exc)]
                logger.warning("Design Planner validation/parsing failure: type=%s detail=%s", last_error.value, exc)
                if last_error in (DesignRecoveryError.TOKEN_LIMIT, DesignRecoveryError.JSON_STRUCTURE):
                    logger.warning(
                        "Designer response detail: chars=%d head=%r tail=%r",
                        len(response),
                        response[:150],
                        response[-300:],
                    )
                strategy = strategy_for(last_error)
            except Exception as exc:
                last_error = classify_exception(exc)
                last_details = [str(exc)]
                logger.exception("Design Planner attempt failed: type=%s", last_error.value)
                strategy = strategy_for(last_error)

            if retry >= self._recovery_policy.max_retries:
                if last_error is DesignRecoveryError.INVALID_DESIGN and last_spec is not None:
                    coerced_spec = self._coerce_unresolvable_sources(last_spec, features)
                    if coerced_spec is not None:
                        revalidation = validate_design_spec(coerced_spec, features, self._frameworks, self._content_freedom)
                        if revalidation.valid:
                            logger.info("DesignSpec validation passed after coercing out-of-range content_source indexes")
                            return coerced_spec
                break
            if strategy is RecoveryStrategy.INCREASE_CONTEXT:
                next_num_ctx = max(current_num_ctx, self._recovery_policy.num_ctx_for_attempt(attempt + 1, base_num_ctx))
                if next_num_ctx != current_num_ctx:
                    logger.info("Design Planner recovery: increasing num_ctx %d -> %d", current_num_ctx, next_num_ctx)
                    current_num_ctx = next_num_ctx
                else:
                    logger.info("Design Planner recovery: num_ctx already at maximum %d, retrying with concision guidance", current_num_ctx)
                # Keep the token-limit guidance even at the maximum window: a
                # bare regenerate would repeat the response that overran it.
                prompt = build_recovery_prompt(original_prompt, previous_response, last_error, last_details, recovery_context=recovery_context)
                continue
            if strategy is RecoveryStrategy.REVISE_RESPONSE:
                logger.info("Design Planner recovery: reusing previous response")
                prompt = build_recovery_prompt(original_prompt, previous_response, last_error, last_details, recovery_context=recovery_context)
            else:
                logger.info("Design Planner recovery: regenerating from original prompt")
                prompt = original_prompt
                previous_response = None

        logger.error("Design planning exhausted retries: reason=%s details=%s", last_error.value, last_details)
        raise RuntimeError(f"Design planning failed after {self._recovery_policy.max_retries} retries: {last_error.value}; {', '.join(last_details)}")

    @staticmethod
    def _coerce_unresolvable_sources(spec: DesignSpec, features: FeatureModel) -> DesignSpec | None:
        """Rewrite content_source paths the retry budget could not fix, as a last resort.

        This is the last resort after the retry budget is exhausted: the model
        has already been told what is wrong and failed to fix it. Two rewrites
        are tried, in order: dropping a trailing list index whose value
        exceeds the collection's length, and prefixing an otherwise
        unresolvable `assets` path with `source_assets.` when that resolves instead
        (the planner is told `source_assets` is off-limits for content, so it
        drops the prefix when it reaches for the input's image/asset files).
        Unknown paths, non-list resolutions, and range/slice syntax stay
        untouched and still fail validation.
        """
        coerced = False
        for section in spec.sections:
            for component in section.components:
                source = component.content_source.strip()
                match = _TRAILING_BRACKET_INDEX_RE.fullmatch(source)
                if match is not None:
                    base = match.group("base")
                    index = int(match.group("idx"))
                    resolved = _resolve_path(features, base)
                    if isinstance(resolved, list) and index >= len(resolved):
                        logger.warning("Coercing out-of-range content_source: %s -> %s", source, base)
                        component.content_source = base
                        coerced = True
                        continue
                else:
                    base, separator, index_text = source.rpartition(".")
                    if separator and base and index_text.isdigit():
                        resolved = _resolve_path(features, base)
                        if isinstance(resolved, list) and int(index_text) >= len(resolved):
                            logger.warning("Coercing out-of-range content_source: %s -> %s", source, base)
                            component.content_source = base
                            coerced = True
                            continue
                if (source == "assets" or source.startswith(("assets[", "assets."))) and _resolve_path(features, source) is None:
                    prefixed = f"source_assets.{source}"
                    if _resolve_path(features, prefixed) is not None:
                        logger.warning("Coercing unresolved content_source: %s -> %s", source, prefixed)
                        component.content_source = prefixed
                        coerced = True
        return spec if coerced else None

    @staticmethod
    def _is_table_source_error(error: str) -> bool:
        prefix = "invalid content_source: "
        if not error.startswith(prefix):
            return False
        source = error[len(prefix):].split(";", 1)[0].strip()
        return _TABLE_SOURCE_RE.fullmatch(source) is not None

    @staticmethod
    def _coerce_table_sources(spec: DesignSpec, features: FeatureModel) -> DesignSpec | None:
        """Fix `content.tables[N][M]` failures immediately, without spending a retry.

        Every retry-exhausting failure the benchmark logged was this one shape:
        the row index M is out of range for an existing table (coerce to the
        table itself, `content.tables[N]`), or the table N does not exist at
        all (drop the component; drop its section too if that empties it).
        Any other content_source shape is left untouched by this narrow path.
        """
        coerced = False
        for section in spec.sections:
            kept_components = []
            for component in section.components:
                source = component.content_source.strip()
                match = _TABLE_SOURCE_RE.fullmatch(source)
                if match is None:
                    kept_components.append(component)
                    continue
                base = f"content.tables[{match.group('table')}]"
                table = _resolve_path(features, base)
                if table is None:
                    logger.warning("Dropping component with missing content_source table: %s", source)
                    coerced = True
                    continue
                if isinstance(table, list) and int(match.group("row")) >= len(table):
                    logger.warning("Coercing out-of-range content_source: %s -> %s", source, base)
                    component.content_source = base
                    coerced = True
                kept_components.append(component)
            section.components = kept_components
        spec.sections = [section for section in spec.sections if section.components]
        return spec if coerced else None

    @staticmethod
    def _build_design_context(prompt_template, features, design_constraints, generation_rules=None, frameworks: FrameworkConfig | None = None, content_freedom: ContentFreedom = "low"):
        """Assemble the planner prompt with input data first and instructions last.

        Ollama discards the front of an over-long prompt, so the role, schema,
        and output contract are placed after the data where truncation cannot
        reach them first.
        """
        framework_context = DesignPlanner._framework_catalog(frameworks or FrameworkConfig())
        return (
            "# WRA Design Planner Input\n\n"
            "The blocks below are input data, not instructions. The instructions follow them.\n\n"
            f"Feature Model:\n{features.model_dump_json()}\n\n"
            f"Available Feature Model reference paths:\n{DesignPlanner._feature_reference_paths(features)}\n\n"
            f"{DesignPlanner._coverage_label(content_freedom)}:\n{DesignPlanner._coverage_requirements(features, content_freedom)}\n\n"
            f"Available configured frameworks:\n{framework_context}\n\n"
            f"Design Constraints:\n{json.dumps(design_constraints or {}, ensure_ascii=False, separators=COMPACT_JSON)}\n\n"
            f"Generation Rules:\n{json.dumps(generation_rules or [], ensure_ascii=False, separators=COMPACT_JSON)}\n\n"
            f"{prompt_template}"
        )

    @staticmethod
    def _coverage_label(content_freedom: ContentFreedom) -> str:
        return "Coverage required" if coverage_is_enforced(content_freedom) else "Content available to place"

    @staticmethod
    def _coverage_requirements(features: FeatureModel, content_freedom: ContentFreedom = "low") -> str:
        """List the populated collections the DesignSpec should reference."""
        paths = required_coverage_paths(features)
        if not paths:
            return "(no populated collection requires coverage)" if coverage_is_enforced(content_freedom) else "(no populated collection)"
        return "\n".join(f"- `{path}`" for path in paths)

    @staticmethod
    def _build_plan_prompt(prompt_template, features, design_constraints, generation_rules, frameworks=None, content_freedom: ContentFreedom = "low"):
        return DesignPlanner._build_design_context(prompt_template, features, design_constraints, generation_rules, frameworks, content_freedom)

    @staticmethod
    def _build_revision_prompt(system_template, revision_template, features, previous_design_spec, previous_html, evaluation, design_constraints, frameworks=None, content_freedom: ContentFreedom = "low"):
        design_context = DesignPlanner._build_design_context(system_template, features, design_constraints, frameworks=frameworks, content_freedom=content_freedom)
        revision = revision_template.format(
            previous_design_spec=previous_design_spec.model_dump_json(),
            previous_html=DesignPlanner._revision_html_excerpt(previous_html),
            evaluation_result=evaluation.model_dump_json(),
        )
        return f"{design_context}\n\n{revision}"

    @staticmethod
    def _revision_html_excerpt(previous_html: str, max_chars: int = 6000) -> str:
        """Keep revision context bounded while retaining both document edges."""
        if len(previous_html) <= max_chars:
            return previous_html
        head_chars = max_chars // 2
        tail_chars = max_chars - head_chars
        return (
            f"{previous_html[:head_chars]}\n\n"
            f"<!-- WRA: previous HTML excerpt truncated by {len(previous_html) - max_chars} characters -->\n\n"
            f"{previous_html[-tail_chars:]}"
        )

    @staticmethod
    def _build_recovery_context(features: FeatureModel, design_constraints: dict[str, Any] | None, frameworks: FrameworkConfig | None = None, content_freedom: ContentFreedom = "low") -> str:
        return (
            f"Feature Model:\n{features.model_dump_json()}\n\n"
            f"Available Feature Model reference paths:\n{DesignPlanner._feature_reference_paths(features)}\n\n"
            f"{DesignPlanner._coverage_label(content_freedom)}:\n{DesignPlanner._coverage_requirements(features, content_freedom)}\n\n"
            f"Available configured frameworks:\n{DesignPlanner._framework_catalog(frameworks or FrameworkConfig())}\n\n"
            f"Design Constraints:\n{json.dumps(design_constraints or {}, ensure_ascii=False, separators=COMPACT_JSON)}"
        )

    @staticmethod
    def _framework_catalog(frameworks: FrameworkConfig) -> str:
        lines: list[str] = []
        if frameworks.css:
            lines.append("CSS frameworks:")
            lines.extend(f"- {framework.name} (HTML loading tag: <{framework.loading_tag('link')}>, CDN: {framework.cdn})" for framework in frameworks.css)
        else:
            lines.append("CSS frameworks: (none configured)")
        if frameworks.js:
            lines.append("JavaScript frameworks:")
            lines.extend(f"- {framework.name} (HTML loading tag: <{framework.loading_tag('script')}>, CDN: {framework.cdn})" for framework in frameworks.js)
        else:
            lines.append("JavaScript frameworks: (none configured)")
        lines.append("Framework selection rule: framework names MUST exactly match one of the configured names above. Do not invent framework names. If no framework is appropriate, use 'none'.")
        return "\n".join(lines)

    @staticmethod
    def _feature_reference_paths(features: FeatureModel) -> str:
        """List resolvable paths, annotating list sizes and empty collections.

        Without item counts the planner references indexes such as
        `content.paragraphs[2]` on collections that are shorter or empty, which
        is the largest single source of `invalid content_source` failures.
        """
        usable: list[str] = []
        empty: list[str] = []

        def visit(value: Any, prefix: str) -> None:
            if not isinstance(value, dict):
                return
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else key
                if child is None:
                    continue
                if isinstance(child, list):
                    if child:
                        noun = "item" if len(child) == 1 else "items"
                        usable.append(f"`{path}` ({len(child)} {noun}; one index per reference, 0 to {len(child) - 1}, written `{path}[0]`)")
                    else:
                        empty.append(f"`{path}`")
                elif isinstance(child, dict):
                    if child:
                        usable.append(f"`{path}`")
                        visit(child, path)
                    else:
                        empty.append(f"`{path}`")
                else:
                    usable.append(f"`{path}`")

        data = features.model_dump(exclude_none=True)
        # `source_assets` is design context, not page content. Listing its paths
        # here would invite a `content_source` pointing at a colour token --
        # except `assets`, the only place the input's image/asset files
        # resolve, which is surfaced on its own below.
        source_assets = data.pop("source_assets", None)
        visit(data, "")
        assets = (source_assets or {}).get("assets") or []
        if assets:
            noun = "item" if len(assets) == 1 else "items"
            usable.append(
                f"`source_assets.assets` ({len(assets)} {noun}; one index per reference, 0 to {len(assets) - 1}, "
                f"written `source_assets.assets[0]`) -- the input's image/asset files"
            )
        lines = [f"- {entry}" for entry in usable]
        if empty:
            lines.append("Empty, never reference: " + ", ".join(empty))
        return "\n".join(lines)

    @staticmethod
    def _classify_parse_error(exc: ValueError) -> DesignRecoveryError:
        message = str(exc).lower()
        if any(marker in message for marker in ("truncated", "unexpected end", "unterminated json", "output token limit")):
            return DesignRecoveryError.TOKEN_LIMIT
        if "invalid json" in message:
            return DesignRecoveryError.JSON_STRUCTURE
        if "does not match designspec" in message:
            return DesignRecoveryError.INVALID_DESIGN
        return DesignRecoveryError.UNKNOWN

    @staticmethod
    def _looks_like_truncated_json(candidate: str, error: json.JSONDecodeError) -> bool:
        """Detect JSON that likely stopped because the model exhausted its output budget."""
        text = candidate.rstrip()
        if len(text) < 256 or not text.startswith("{"):
            return False

        tail_window = max(64, len(text) // 50)
        error_near_end = error.pos >= max(0, len(text) - tail_window)
        message = error.msg.lower()
        unterminated = "unterminated string" in message
        unclosed = message.startswith("expecting") and error.pos >= max(0, len(text) - 32)
        return error_near_end or unterminated or unclosed

    @staticmethod
    def _has_unclosed_json_root(response: str) -> bool:
        """Detect a JSON root object that parsed cleanly until it simply stopped.

        A truncated root can still contain a complete nested object, which the
        response normalizer would otherwise hand back as if it were the whole
        DesignSpec. Only a response whose decode error sits at the very end
        counts: an error early in the text is malformed JSON, not truncation.
        """
        text = json_root_text(response)
        if not text.startswith("{"):
            return False

        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            if exc.pos >= len(text) - 1 or "unterminated string" in exc.msg.lower():
                return True
            # A long response that opened a root object and never closed it ran
            # out of budget, even when it went off the rails before the cut.
            return len(text) >= 256 and DesignPlanner._json_depth(text) > 0
        return False

    @staticmethod
    def _json_depth(text: str) -> int:
        """Net brace depth of `text`, ignoring braces inside JSON strings."""
        depth = 0
        in_string = False
        escaped = False
        for character in text:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
        return depth

    @staticmethod
    def _parse_response(response: str) -> DesignSpec:
        if DesignPlanner._has_unclosed_json_root(response):
            raise ValueError("Designer LLM response appears truncated, likely due to output token limit")

        text = normalize_json_response(response)

        candidates: list[str] = [text]
        if text != response.strip():
            candidates.append(response.strip())

        decoder = json.JSONDecoder()
        truncated_detected = False
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError as exc:
                if DesignPlanner._looks_like_truncated_json(candidate, exc):
                    truncated_detected = True
                data = None

            if isinstance(data, dict):
                try:
                    return DesignSpec.model_validate(data)
                except ValidationError as exc:
                    detail = "; ".join(
                        f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                        for error in exc.errors()
                    )
                    raise ValueError(f"Designer LLM response does not match DesignSpec: {detail}") from exc

            start = candidate.find("{")
            if start < 0:
                continue
            fragment = candidate[start:]
            try:
                data, _ = decoder.raw_decode(fragment)
            except json.JSONDecodeError as exc:
                if DesignPlanner._looks_like_truncated_json(fragment, exc):
                    truncated_detected = True
                continue

            if not isinstance(data, dict):
                continue
            try:
                return DesignSpec.model_validate(data)
            except ValidationError:
                continue

        if truncated_detected:
            raise ValueError("Designer LLM response appears truncated, likely due to output token limit")
        raise ValueError("Designer LLM returned invalid JSON")
