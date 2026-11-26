import re

from wra.designer.schema import DesignSpec
from wra.validator.craft import _colors_in

THEME_STYLE_ID = "wra-theme"

_FIELDS = (
    ("--color-bg", "color_system", "background"),
    ("--color-surface", "color_system", "surface"),
    ("--color-text", "color_system", "text"),
    ("--color-muted", "color_system", "muted_text"),
    ("--color-primary", "color_system", "primary"),
    ("--color-accent", "color_system", "accent"),
    ("--font-heading", "typography", "heading"),
    ("--font-body", "typography", "body"),
)

_FONT_STACK = '"Noto Sans KR", system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif'

DEFAULT_TOKENS = {
    "--color-bg": "#ffffff",
    "--color-surface": "#f4f6f8",
    "--color-text": "#1f2430",
    "--color-muted": "#566070",
    "--color-primary": "#1f4fd8",
    "--color-accent": "#0b7a75",
    "--font-heading": _FONT_STACK,
    "--font-body": _FONT_STACK,
}

_COLOR_VALUE_RE = re.compile(r"#[0-9a-f]{3,8}|(?:rgb|hsl)a?\([^()]*\)", re.IGNORECASE)
_GENERIC_FAMILY_RE = re.compile(r"\b(?:sans-serif|serif|monospace|system-ui|cursive|fantasy)\b", re.IGNORECASE)
_BOOTSTRAP_BRIDGE = (
    ("--bs-body-bg", "--color-bg"),
    ("--bs-body-color", "--color-text"),
    ("--bs-primary", "--color-primary"),
    ("--bs-link-color", "--color-primary"),
    ("--bs-secondary-color", "--color-muted"),
    ("--bs-tertiary-bg", "--color-surface"),
)
_THEME_STYLE_RE = re.compile(rf"<style\b[^>]*\bid\s*=\s*[\"']?{THEME_STYLE_ID}[\"']?[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)
_HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_BLANKED_RE = re.compile(r"<script\b.*?</script\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL)
_AUTHOR_STYLE_RE = re.compile(r"""<style\b|<link\b(?=[^>]*\brel\s*=\s*["']?stylesheet)(?![^>]*\bhref\s*=\s*["']?(?:https?:)?//)""", re.IGNORECASE)


def _spec_value(design_spec: DesignSpec, group: str, field: str) -> str:
    return str(getattr(getattr(design_spec.design, group), field, "") or "").strip().rstrip(";").strip()


def _usable_color(value: str) -> bool:
    return bool(_COLOR_VALUE_RE.fullmatch(value)) and bool(_colors_in(value))


def _usable_font(value: str) -> bool:
    if not value or len(value) > 120 or re.search(r"[;{}<>]", value):
        return False
    return all(part.strip() and len(part.strip().split()) <= 4 and len(part.strip()) <= 40 for part in value.split(","))


def resolved_tokens(design_spec: DesignSpec) -> dict[str, str]:
    """Each token from the design spec when it is a usable colour or font list, else the neutral default."""
    tokens = {}
    for token, group, field in _FIELDS:
        value = _spec_value(design_spec, group, field)
        if token.startswith("--color-"):
            tokens[token] = value if _usable_color(value) else DEFAULT_TOKENS[token]
        elif _usable_font(value):
            tokens[token] = value if _GENERIC_FAMILY_RE.search(value) else f"{value}, sans-serif"
        else:
            tokens[token] = DEFAULT_TOKENS[token]
    return tokens


def root_block(design_spec: DesignSpec) -> str:
    declarations = []
    for token, group, field in _FIELDS:
        value = _spec_value(design_spec, group, field)
        if value:
            declarations.append(f"{token}:{value}")
    if not declarations:
        return ""
    return ":root{" + ";".join(declarations) + ";}"


def injected_root_block(design_spec: DesignSpec) -> str:
    return ":root{" + ";".join(f"{token}:{value}" for token, value in resolved_tokens(design_spec).items()) + ";}"


def _is_bootstrap(design_spec: DesignSpec) -> bool:
    return "bootstrap" in design_spec.design.framework.css.name.lower()


def _skeleton_css(design_spec: DesignSpec) -> str:
    tokens = resolved_tokens(design_spec)
    declarations = [f"{token}:{value}" for token, value in tokens.items()]
    if _is_bootstrap(design_spec):
        declarations += [f"{bootstrap}:var({token})" for bootstrap, token in _BOOTSTRAP_BRIDGE]
    return (
        ":root{" + ";".join(declarations) + ";}\n"
        "body{background:var(--color-bg);color:var(--color-text);font-family:var(--font-body)}\n"
        "h1,h2,h3,h4,h5,h6{font-family:var(--font-heading)}\n"
        "a{color:var(--color-primary)}"
    )


def ensure_theme_skeleton(html: str, design_spec: DesignSpec) -> tuple[str, list[str]]:
    """Give the page a `<style id="wra-theme">` carrying the resolved design tokens and a base layer.

    It sits ahead of the page's own styles and local stylesheets, after any CDN
    framework sheet, so the framework defaults give way to the design while the
    author's own rules still win. An existing block is rewritten in place.
    """
    block = f'<style id="{THEME_STYLE_ID}" data-wra-repair="skeleton">\n{_skeleton_css(design_spec)}\n</style>'
    existing = list(_THEME_STYLE_RE.finditer(html))
    if existing:
        rebuilt = html
        for match in reversed(existing[1:]):
            rebuilt = rebuilt[:match.start()] + rebuilt[match.end():]
        first = existing[0]
        rebuilt = rebuilt[:first.start()] + block + rebuilt[first.end():]
        return rebuilt, ([] if rebuilt == html else ["refreshed wra-theme tokens"])
    close = _HEAD_CLOSE_RE.search(html)
    if close is not None:
        head = _BLANKED_RE.sub(lambda match: " " * len(match.group(0)), html[:close.start()])
        author = _AUTHOR_STYLE_RE.search(head)
        position = author.start() if author else close.start()
    else:
        opening = _HEAD_OPEN_RE.search(html)
        if opening is None:
            return html, []
        position = opening.end()
    return f"{html[:position]}{block}\n{html[position:]}", ["inserted wra-theme tokens"]
