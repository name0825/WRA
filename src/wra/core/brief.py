from dataclasses import dataclass, field
from pathlib import Path
import logging
import re

import yaml

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class BriefDirectives:
    """Design decisions a structured brief fixes, as opposed to prose it leaves open."""

    palette: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, str] = field(default_factory=dict)
    logo: str | None = None
    layout: list[str] = field(default_factory=list)
    must: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    nav: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.palette or self.fonts or self.logo or self.layout or self.must or self.avoid or self.nav)


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip()]


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _parse(data) -> BriefDirectives | None:
    if not isinstance(data, dict):
        return None
    design = _mapping(data.get("design"))
    palette = {str(key): value.strip() for key, value in _mapping(design.get("palette")).items() if isinstance(value, str) and _HEX_RE.match(value.strip())}
    fonts = {str(key): value.strip() for key, value in _mapping(design.get("typography")).items() if isinstance(value, str) and value.strip()}
    logo = design.get("logo") or _mapping(data.get("site")).get("logo")
    directives = BriefDirectives(
        palette=palette,
        fonts=fonts,
        logo=logo.strip() if isinstance(logo, str) and logo.strip() else None,
        layout=_strings(design.get("layout")),
        must=_strings(design.get("must")),
        avoid=_strings(design.get("avoid")),
        nav=_strings(data.get("nav")),
    )
    return None if directives.is_empty() else directives


def parse_brief_directives(files) -> BriefDirectives | None:
    """Read the design directives out of the first YAML brief that has any.

    Anything that is not a well-formed YAML mapping, markdown and plain text
    included, yields None and leaves the raw brief text to do the work.
    """
    for item in files:
        if item.kind != "brief" or Path(item.relative).suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            data = yaml.safe_load(item.path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            logger.info("Brief is not valid YAML, using it as text: %s", item.relative)
            continue
        directives = _parse(data)
        if directives is not None:
            return directives
    return None
