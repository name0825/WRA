import logging
from dataclasses import dataclass, field

from wra.core.models import FeatureModel
from wra.validator.structure import validate_structure

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HTMLValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_html(html: str, features: FeatureModel) -> HTMLValidationResult:
    logger.info("HTML validation started: chars=%d", len(html))
    errors: list[str] = []
    warnings: list[str] = []

    if not html.strip():
        result = HTMLValidationResult(valid=False, errors=["HTML is empty"])
        logger.warning("HTML validation failed: empty HTML")
        return result

    if "<!doctype html>" not in html.lower():
        errors.append("missing DOCTYPE")

    structure_errors, structure_warnings = validate_structure(html)
    errors.extend(structure_errors)
    warnings.extend(structure_warnings)

    result = HTMLValidationResult(valid=not errors, errors=errors, warnings=warnings)
    if result.valid:
        logger.info("HTML validation passed: warnings=%d", len(warnings))
    else:
        logger.warning("HTML validation failed: errors=%s warnings=%s", errors, warnings)
    return result
