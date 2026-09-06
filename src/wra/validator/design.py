from dataclasses import dataclass, field
import logging
import re

from wra.core.models import ContentFreedom, FeatureModel, FrameworkConfig, coverage_is_enforced
from wra.designer.schema import DesignSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesignValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


_PATH_PART_RE = re.compile(r"^(?P<name>[^\[\]]+)(?P<indexes>(?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")

# Collections a DesignSpec must reference when populated.
#
# `media.urls` and `media.icons` are intentionally excluded: `media.urls` is a
# verbatim duplicate of the image/video `src` values already covered by
# `media.images`/`media.videos`, and `media.icons` holds CSS class strings
# rather than designable content. Requiring separate coverage for either forces
# the planner to invent filler components.
REQUIRED_COVERAGE_COLLECTIONS = (
    "content.headings",
    "content.paragraphs",
    "content.lists",
    "content.tables",
    "content.buttons",
    "content.links",
    "content.labels",
    "content.text",
    "structure.sections",
    "structure.forms",
    "media.images",
    "media.videos",
)


# A path whose last step is an index, in either form the planners write:
# `media.images[8]` and `media.images.8` name the same item.
_TRAILING_INDEX_RE = re.compile(r"(?P<collection>.+?)(?:\[(?P<bracket>\d+)\]|\.(?P<dotted>\d+))")


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

        if isinstance(current, list) and name.isdigit() and not indexes:
            # Planners write list indexes as `media.images.0` as often as
            # `media.images[0]`; both name the same item.
            index = int(name)
            if index >= len(current):
                return None
            current = current[index]
            continue

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


def _framework_hint(name: str, frameworks: FrameworkConfig, slot: str) -> str:
    """What the designer needs to fix the name, not just be told it is wrong.

    Naming the problem alone is not recoverable: told only that "Tailwind" is
    not configured, the designer returns "Tailwind" again and the run dies with
    its retries spent. The message has to carry the alternatives, and the most
    likely mistake is a framework put in the wrong slot.
    """
    catalog, other_slot = (frameworks.css, "JavaScript") if slot == "CSS" else (frameworks.js, "CSS")
    other = frameworks.find_js(name) if slot == "CSS" else frameworks.find_css(name)
    listed = ", ".join(framework.name for framework in catalog) or "(none)"
    misplaced = f"; {other.name} is a configured {other_slot} framework, so it belongs in the {other_slot} slot" if other is not None else ""
    return f"{misplaced}; configured {slot} frameworks are: {listed}"


def _source_hint(features: object, source: str) -> str:
    """Why the path did not resolve, in terms the planner can act on.

    Naming the path alone is not recoverable. Told only that
    `media.images[8]` is invalid, the planner tried `[9]`, then `[8]` three
    times over, and the run died with its retries spent -- on a collection whose
    highest index is 6. The limit has to travel with the error.
    """
    match = _TRAILING_INDEX_RE.fullmatch(source)
    if match is None:
        return ""
    collection = match.group("collection")
    resolved = _resolve_path(features, collection)
    if isinstance(resolved, list):
        if not resolved:
            return f"; {collection} is empty and has no valid index"
        return f"; {collection} holds {len(resolved)} items, so its highest index is {len(resolved) - 1}"
    if resolved is None:
        return f"; {collection} does not resolve either"
    return f"; {collection} is not a collection and takes no index"


def _validate_frameworks(spec: DesignSpec, frameworks: FrameworkConfig, errors: list[str]) -> None:
    css_name = spec.design.framework.css.name.strip()
    if not _is_no_framework(css_name) and frameworks.find_css(css_name) is None:
        errors.append(f"CSS framework is not configured: {css_name}{_framework_hint(css_name, frameworks, 'CSS')}")

    js_name = spec.design.framework.js.name.strip()
    if not _is_no_framework(js_name) and frameworks.find_js(js_name) is None:
        errors.append(f"JavaScript framework is not configured: {js_name}{_framework_hint(js_name, frameworks, 'JavaScript')}")


def _source_covers_collection(source: str, collection_path: str) -> bool:
    """Return whether a source references the collection or anything below it."""
    return (
        source == collection_path
        or source.startswith(f"{collection_path}.")
        or source.startswith(f"{collection_path}[")
    )


def required_coverage_paths(features: FeatureModel) -> list[str]:
    """Return the populated collections a DesignSpec must reference."""
    feature_data = features.model_dump(exclude_none=True)
    return [path for path in REQUIRED_COVERAGE_COLLECTIONS if _resolve_path(feature_data, path)]


def _validate_content_coverage(spec: DesignSpec, features: FeatureModel, errors: list[str]) -> None:
    """Ensure populated FeatureModel collections are represented by a component source."""
    sources = {
        component.content_source.strip()
        for section in spec.sections
        for component in section.components
        if component.content_source.strip()
    }

    feature_data = features.model_dump(exclude_none=True)
    for path in REQUIRED_COVERAGE_COLLECTIONS:
        value = _resolve_path(feature_data, path)
        if not value:
            continue
        if not any(_source_covers_collection(source, path) for source in sources):
            errors.append(f"missing content coverage: {path}")


def validate_design_spec(spec: DesignSpec, features: FeatureModel, frameworks: FrameworkConfig, content_freedom: ContentFreedom = "low") -> DesignValidationResult:
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
                errors.append(f"invalid content_source: {source}{_source_hint(features, source)}")

    for interaction in spec.interaction.components:
        if interaction.target not in component_types:
            declared = ", ".join(sorted(component_types)) or "(none declared)"
            errors.append(f"invalid interaction target: {interaction.target}; declared component types are: {declared}")
    for breakpoint in spec.responsive.breakpoints:
        if not breakpoint.width.strip():
            errors.append(f"empty breakpoint width: {breakpoint.name}")

    _validate_frameworks(spec, frameworks, errors)
    if coverage_is_enforced(content_freedom):
        _validate_content_coverage(spec, features, errors)
    else:
        logger.debug("Content coverage not enforced at freedom level: %s", content_freedom)

    result = DesignValidationResult(valid=not errors, errors=errors)
    if result.valid:
        logger.debug("DesignSpec validation passed")
    else:
        logger.warning("DesignSpec validation failed: errors=%s", errors)
    return result
