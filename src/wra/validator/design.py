from dataclasses import dataclass, field
import logging
import re

from wra.core.models import FeatureModel, FrameworkConfig
from wra.designer.schema import DesignSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesignValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


_PATH_PART_RE = re.compile(r"^(?P<name>[^\[\]]+)(?P<indexes>(?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def _resolve_path(value: object, path: str) -> object | None:
    """Resolve dotted FeatureModel paths with optional list indexes."""
    current = value
    for part in path.split("."):
        if not part or current is None:
            return None

        match = _PATH_PART_RE.fullmatch(part)
        if not match:
            return None

        name = match.group("name")
        indexes = _INDEX_RE.findall(match.group("indexes"))

        if isinstance(current, dict):
            if name not in current:
                return None
            current = current[name]
        elif hasattr(current, name):
            current = getattr(current, name)
        else:
            return None

        for index_text in indexes:
            if not isinstance(current, list):
                return None
            index = int(index_text)
            if index < 0 or index >= len(current):
                return None
            current = current[index]

    return current


def _is_no_framework(name: str) -> bool:
    return name.strip().lower() in {"", "none", "vanilla", "vanilla css", "vanilla js", "vanilla javascript"}


def _validate_frameworks(spec: DesignSpec, frameworks: FrameworkConfig, errors: list[str]) -> None:
    css_name = spec.design.framework.css.name.strip()
    if not _is_no_framework(css_name) and frameworks.find_css(css_name) is None:
        errors.append(f"CSS framework is not configured: {css_name}")

    js_name = spec.design.framework.js.name.strip()
    if not _is_no_framework(js_name) and frameworks.find_js(js_name) is None:
        errors.append(f"JavaScript framework is not configured: {js_name}")


def _source_covers_collection(source: str, collection_path: str) -> bool:
    """Return whether a source references the collection or anything below it."""
    return (
        source == collection_path
        or source.startswith(f"{collection_path}.")
        or source.startswith(f"{collection_path}[")
    )


def _validate_content_coverage(spec: DesignSpec, features: FeatureModel, errors: list[str]) -> None:
    """Ensure populated content/media collections are represented by a component source."""
    sources = {
        component.content_source.strip()
        for section in spec.sections
        for component in section.components
        if component.content_source.strip()
    }

    required_collections = (
        "content.headings",
        "content.paragraphs",
        "content.lists",
        "content.tables",
        "content.buttons",
        "content.links",
        "content.labels",
        "content.text",
        "media.images",
        "media.videos",
        "media.icons",
        "media.urls",
    )

    feature_data = features.model_dump(exclude_none=True)
    for path in required_collections:
        value = _resolve_path(feature_data, path)
        if not value:
            continue
        if not any(_source_covers_collection(source, path) for source in sources):
            errors.append(f"missing content coverage: {path}")


def validate_design_spec(spec: DesignSpec, features: FeatureModel, frameworks: FrameworkConfig) -> DesignValidationResult:
    logger.debug("DesignSpec validation started: sections=%d", len(spec.sections))
    errors: list[str] = []

    if not spec.sections:
        errors.append("sections must contain at least one section")

    section_ids = [section.id for section in spec.sections]
    duplicates = {section_id for section_id in section_ids if section_ids.count(section_id) > 1}
    errors.extend(f"duplicate section id: {section_id}" for section_id in sorted(duplicates))

    for section in spec.sections:
        if not section.components:
            errors.append(f"section has no components: {section.id}")

    component_types = {component.type for section in spec.sections for component in section.components if component.type}
    for section in spec.sections:
        for component in section.components:
            source = component.content_source.strip()
            if not source:
                errors.append(f"empty content_source: {component.type}")
                continue
            if _resolve_path(features, source) is None:
                errors.append(f"invalid content_source: {source}")

    for interaction in spec.interaction.components:
        if interaction.target not in component_types:
            errors.append(f"invalid interaction target: {interaction.target}")
    for breakpoint in spec.responsive.breakpoints:
        if not breakpoint.width.strip():
            errors.append(f"empty breakpoint width: {breakpoint.name}")

    _validate_frameworks(spec, frameworks, errors)
    _validate_content_coverage(spec, features, errors)

    result = DesignValidationResult(valid=not errors, errors=errors)
    if result.valid:
        logger.debug("DesignSpec validation passed")
    else:
        logger.warning("DesignSpec validation failed: errors=%s", errors)
    return result
