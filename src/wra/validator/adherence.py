import re
from urllib.parse import unquote

from wra.core.models import FrameworkConfig
from wra.designer.schema import DesignSpec
from wra.validator.craft import _colors_in
from wra.validator.syntax import _SCRIPT_BLOCK_RE, _STYLE_ATTR_RE, _STYLE_BLOCK_RE, _extract_css_text
from wra.validator.tokens import extract_visible_text
from wra.validator.visual import _references_image

FRAMEWORK_THEME_PREFIX = "framework theme not applied"
DIRECTIVE_PREFIX = "brief directive not met"

_CLASS_ATTR_RE = re.compile(r'class\s*=\s*"([^"]*)"', re.IGNORECASE)
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)


def _theme_text(html: str) -> str:
    return "\n".join([_extract_css_text(html), *_CLASS_ATTR_RE.findall(html), *_SCRIPT_BLOCK_RE.findall(html)])


def find_unapplied_design_tokens(html: str, design_spec: DesignSpec, frameworks: FrameworkConfig) -> list[str]:
    """Flag a page that loads the designed CSS framework but uses none of the designed colours."""
    if frameworks.find_css(design_spec.design.framework.css.name) is None:
        return []
    designed = {color for value in design_spec.design.color_system.model_dump().values() for color in _colors_in(str(value))}
    if not designed:
        return []
    if designed & set(_colors_in(_theme_text(html))):
        return []
    return [
        f"{FRAMEWORK_THEME_PREFIX}: none of the designed colours ({', '.join(sorted(designed))}) appear in the page's CSS, "
        f"so the {design_spec.design.framework.css.name} defaults are showing instead of the design"
    ]


def _font_haystack(html: str) -> str:
    raw = "\n".join([*_STYLE_BLOCK_RE.findall(html), *_STYLE_ATTR_RE.findall(html), *_LINK_TAG_RE.findall(html)])
    return re.sub(r"[^a-z0-9가-힣]", "", unquote(raw).lower())


def find_directive_deviations(html: str, palette: list[str], fonts: list[str], logo: str | None, nav: list[str]) -> list[str]:
    """Brief-level palette, font, logo and navigation directives the page does not carry."""
    findings: list[str] = []

    wanted = {color for entry in palette for color in _colors_in(entry)}
    if wanted:
        present = set(_colors_in(_theme_text(html)))
        missing = sorted(wanted - present)
        if missing:
            findings.append(f"{DIRECTIVE_PREFIX}: palette colours not used in the page CSS: {', '.join(missing)}")

    if fonts:
        haystack = _font_haystack(html)
        missing_fonts = [font for font in dict.fromkeys(fonts) if re.sub(r"[^a-z0-9가-힣]", "", font.lower()) not in haystack]
        if missing_fonts:
            findings.append(f"{DIRECTIVE_PREFIX}: fonts not referenced in the page CSS: {', '.join(missing_fonts)}")

    if logo:
        reference = logo.replace("\\", "/")
        if not _references_image(html, reference) and reference not in html:
            findings.append(f"{DIRECTIVE_PREFIX}: logo {reference} is not referenced by the page")

    if nav:
        visible = extract_visible_text(html).lower()
        missing_nav = [label for label in dict.fromkeys(nav) if label.strip() and label.strip().lower() not in visible]
        if missing_nav:
            findings.append(f"{DIRECTIVE_PREFIX}: navigation labels not on the page: {', '.join(missing_nav)}")

    return findings
