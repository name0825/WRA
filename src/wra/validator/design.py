from dataclasses import dataclass, field
import logging

from wra.core.models import FeatureModel
from wra.designer.schema import DesignSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesignValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def _resolve_path(value: object, path: str) -> object | None:
    current = value
    for part in path.split("."):
        if not part or current is None:
            return None
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


def validate_design_spec(spec: DesignSpec, features: FeatureModel) -> DesignValidationResult:
    logger.debug("DesignSpec validation started: sections=%d", len(spec.sections))
    errors: list[str] = []
    section_ids = [section.id for section in spec.sections]
    duplicates = {section_id for section_id in section_ids if section_ids.count(section_id) > 1}
    errors.extend(f"duplicate section id: {section_id}" for section_id in sorted(duplicates))

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

    result = DesignValidationResult(valid=not errors, errors=errors)
    if result.valid:
        logger.debug("DesignSpec validation passed")
    else:
        logger.warning("DesignSpec validation failed: errors=%s", errors)
    return result
