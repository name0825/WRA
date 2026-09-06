from collections import Counter
from pathlib import Path
import logging
import re
import struct

from wra.core.inputs import InputBundle
from wra.core.models import AssetInfo, ScriptDigest, StyleDigest

logger = logging.getLogger(__name__)

_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\)")
_CUSTOM_PROPERTY_RE = re.compile(r"(--[a-z0-9_-]+)\s*:\s*([^;}]+)", re.I)
_FONT_STACK_RE = re.compile(r"(sans-serif|serif|monospace|cursive|system-ui)\s*$", re.I)
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}]+)", re.I)
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([^;}]+)", re.I)
_SPACING_RE = re.compile(r"(?:margin|padding|gap|row-gap|column-gap)[a-z-]*\s*:\s*([^;}]+)", re.I)
_RADIUS_RE = re.compile(r"border-radius\s*:\s*([^;}]+)", re.I)
_SHADOW_RE = re.compile(r"box-shadow\s*:\s*([^;}]+)", re.I)
_MEDIA_RE = re.compile(r"@media[^{]*?\(\s*(min-width|max-width)\s*:\s*([^)]+)\)", re.I)
_LENGTH_RE = re.compile(r"^-?\d*\.?\d+(px|rem|em|%|vw|vh|pt)$")

_LAYOUT_SIGNALS = {
    "grid": re.compile(r"display\s*:\s*(inline-)?grid", re.I),
    "flex": re.compile(r"display\s*:\s*(inline-)?flex", re.I),
    "sticky": re.compile(r"position\s*:\s*sticky", re.I),
    "fixed": re.compile(r"position\s*:\s*fixed", re.I),
    "transition": re.compile(r"\btransition\s*:", re.I),
    "animation": re.compile(r"@keyframes\b", re.I),
    "custom_properties": re.compile(r"--[a-z0-9-]+\s*:", re.I),
    "clamp": re.compile(r"\bclamp\(", re.I),
    "backdrop_filter": re.compile(r"backdrop-filter\s*:", re.I),
    "gradient": re.compile(r"\b(linear|radial|conic)-gradient\(", re.I),
}

_LIBRARY_SIGNATURES = {
    "jQuery": re.compile(r"\bjQuery\b|\$\(\s*(document|window|['\"])"),
    "GSAP": re.compile(r"\bgsap\b|\bTweenMax\b", re.I),
    "ScrollTrigger": re.compile(r"\bScrollTrigger\b"),
    "Swiper": re.compile(r"\bnew\s+Swiper\b|\bSwiper\(", re.I),
    "Slick": re.compile(r"\.slick\(", re.I),
    "AOS": re.compile(r"\bAOS\.init\b"),
    "Bootstrap": re.compile(r"\bbootstrap\.(Modal|Collapse|Dropdown|Carousel)\b", re.I),
    "Lottie": re.compile(r"\blottie\b", re.I),
    "Three.js": re.compile(r"\bTHREE\.", re.I),
    "Chart.js": re.compile(r"\bnew\s+Chart\(", re.I),
    "React": re.compile(r"\bReactDOM\b|\bReact\.createElement\b"),
    "Vue": re.compile(r"\bnew\s+Vue\(|\bVue\.createApp\b"),
    "Alpine.js": re.compile(r"\bAlpine\b"),
    "Kakao Maps": re.compile(r"\bkakao\.maps\b", re.I),
    "Naver Maps": re.compile(r"\bnaver\.maps\b", re.I),
    "Google Maps": re.compile(r"\bgoogle\.maps\b", re.I),
}

_INTERACTION_SIGNATURES = {
    "scroll effects": re.compile(r"addEventListener\(\s*['\"]scroll|onscroll|ScrollTrigger|IntersectionObserver"),
    "click handlers": re.compile(r"addEventListener\(\s*['\"]click|onclick"),
    "hover effects": re.compile(r"addEventListener\(\s*['\"]mouse(enter|over)"),
    "responsive resize": re.compile(r"addEventListener\(\s*['\"]resize|matchMedia"),
    "keyboard handling": re.compile(r"addEventListener\(\s*['\"]key"),
    "form submission": re.compile(r"addEventListener\(\s*['\"]submit|\.submit\(|preventDefault"),
    "form validation": re.compile(r"\bvalid(ate|ity)\b|required\b.*checked|checkValidity", re.I),
    "modal / dialog": re.compile(r"\bmodal\b|\bdialog\b|\bpopup\b|\blayer-?pop\b", re.I),
    "carousel / slider": re.compile(r"\bcarousel\b|\bslider\b|\bswiper\b|\bslide(Next|Prev|To)\b", re.I),
    "tabs": re.compile(r"\btab(-|_)?(btn|menu|content|index)\b|\btabs\b", re.I),
    "accordion": re.compile(r"\baccordion\b|\bcollapse\b|\btoggle-?(faq|answer)\b", re.I),
    "menu toggle": re.compile(r"\b(nav|menu|gnb|hamburger)\b[^;\n]{0,40}\b(toggle|open|active)\b", re.I),
    "class toggling": re.compile(r"classList\.(toggle|add|remove)"),
    "smooth scrolling": re.compile(r"scrollIntoView|scrollTo\(\s*\{|behavior\s*:\s*['\"]smooth"),
    "counters": re.compile(r"\bcount(er|Up)\b|setInterval\([^)]*\+\+", re.I),
    "lazy loading": re.compile(r"\blazy(load)?\b|loading\s*=\s*['\"]lazy", re.I),
    "parallax": re.compile(r"\bparallax\b", re.I),
    "async data fetch": re.compile(r"\bfetch\(|XMLHttpRequest|\$\.ajax"),
}


def _top(values, limit: int) -> list[str]:
    return [value for value, _ in Counter(values).most_common(limit)]


def _normalize_length(value: str) -> str | None:
    token = value.strip().rstrip(";").strip()
    if token.startswith("."):
        token = f"0{token}"
    elif token.startswith("-."):
        token = f"-0{token[1:]}"
    return token if _LENGTH_RE.match(token) else None


def _custom_properties(css: str) -> tuple[list[str], list[str], set[str]]:
    """Read the palette and font stacks off the source's own custom properties.

    A design system defined as `--navy: #2B4884` and referenced through `var()`
    shows every literal exactly once, so raw frequency counting reports a flat,
    meaningless palette. The declarations carry the author's own naming, which
    is the more useful signal.
    """
    colors: list[str] = []
    fonts: list[str] = []
    literals: set[str] = set()
    named: set[str] = set()
    for name, raw in _CUSTOM_PROPERTY_RE.findall(css):
        value = " ".join(raw.split()).rstrip(";").strip()
        if name in named:
            # A property redeclared in a fallback or media block is the same
            # token, not a second one.
            continue
        color = _COLOR_RE.fullmatch(value.replace(" ", "")) if value else None
        if color:
            colors.append(f"{name}: {value}")
            literals.add(_normalize_color(value))
            named.add(name)
        elif _FONT_STACK_RE.search(value) or ('"' in value and "," in value):
            family = value.split(",")[0].strip().strip("'\"").strip()
            if family:
                fonts.append(f"{name}: {family}")
                named.add(name)
    return colors, fonts, literals


def _sort_key(value: str) -> float:
    """First number anywhere in the value: breakpoints read `max-width: 620px`."""
    match = re.search(r"-?\d*\.?\d+", value)
    return float(match.group(0)) if match else 0.0


def _normalize_color(value: str) -> str:
    token = value.replace(" ", "").lower()
    if re.fullmatch(r"#[0-9a-f]{3}", token):
        return "#" + "".join(channel * 2 for channel in token[1:])
    return token


def build_style_digest(css: str) -> StyleDigest:
    """Distil the source CSS into design tokens. The CSS text itself never leaves here."""
    if not css.strip():
        return StyleDigest()

    named_colors, named_fonts, declared = _custom_properties(css)
    counts = Counter(match.group(0).lower().replace(" ", "") for match in _COLOR_RE.finditer(css))
    literal_palette = [f"{color} x{count}" for color, count in counts.most_common(24) if _normalize_color(color) not in declared]
    palette = (named_colors + literal_palette)[:14]

    fonts = list(named_fonts)
    for declaration in _FONT_FAMILY_RE.findall(css):
        first = declaration.split(",")[0].strip().strip("'\"").strip()
        if first and not first.startswith("var(") and first.lower() not in {"inherit", "initial", "unset"}:
            fonts.append(first)

    sizes = {size for size in (_normalize_length(value) for value in _FONT_SIZE_RE.findall(css)) if size}
    spacing: list[str] = []
    for declaration in _SPACING_RE.findall(css):
        spacing.extend(token for token in (_normalize_length(part) for part in declaration.split()) if token)

    radii = [token for token in (_normalize_length(value) for value in _RADIUS_RE.findall(css)) if token]
    shadows = [" ".join(value.split())[:60] for value in _SHADOW_RE.findall(css) if value.strip().lower() != "none"]
    breakpoints = sorted({f"{feature.lower()}: {value.strip()}" for feature, value in _MEDIA_RE.findall(css)}, key=_sort_key)
    layout_signals = {name: len(pattern.findall(css)) for name, pattern in _LAYOUT_SIGNALS.items()}

    digest = StyleDigest(
        palette=palette,
        fonts=_top(fonts, 8),
        type_scale=sorted(sizes, key=_sort_key)[:12],
        spacing=_top(spacing, 10),
        radii=_top(radii, 6),
        shadows=_top(shadows, 4),
        breakpoints=breakpoints[:8],
        layout_signals={name: count for name, count in layout_signals.items() if count},
    )
    logger.info("Style digest built: css_chars=%d colors=%d fonts=%d breakpoints=%d", len(css), len(palette), len(digest.fonts), len(digest.breakpoints))
    return digest


def build_script_digest(js: str) -> ScriptDigest:
    """Describe what the source JavaScript does. Its code never leaves here."""
    if not js.strip():
        return ScriptDigest()
    libraries = [name for name, pattern in _LIBRARY_SIGNATURES.items() if pattern.search(js)]
    interactions = [name for name, pattern in _INTERACTION_SIGNATURES.items() if pattern.search(js)]
    logger.info("Script digest built: js_chars=%d libraries=%d interactions=%d", len(js), len(libraries), len(interactions))
    return ScriptDigest(libraries=libraries, interactions=interactions[:12])


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read pixel dimensions from an image header without an imaging dependency."""
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
            if header[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", header[16:24])
                return int(width), int(height)
            if header[:6] in {b"GIF87a", b"GIF89a"}:
                width, height = struct.unpack("<HH", header[6:10])
                return int(width), int(height)
            if header[:2] == b"BM":
                handle.seek(18)
                width, height = struct.unpack("<ii", handle.read(8))
                return abs(width), abs(height)
            if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
                return _webp_dimensions(handle, header)
            if header[:2] == b"\xff\xd8":
                return _jpeg_dimensions(handle)
            if path.suffix.lower() == ".svg":
                return _svg_dimensions(path)
    except (OSError, struct.error, ValueError):
        logger.warning("Image dimensions unreadable: %s", path)
    return None, None


def _webp_dimensions(handle, header: bytes) -> tuple[int | None, int | None]:
    chunk = header[12:16]
    handle.seek(20)
    payload = handle.read(16)
    if chunk == b"VP8X":
        width = int.from_bytes(payload[4:7], "little") + 1
        height = int.from_bytes(payload[7:10], "little") + 1
        return width, height
    if chunk == b"VP8L":
        bits = int.from_bytes(payload[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        return int.from_bytes(payload[6:8], "little") & 0x3FFF, int.from_bytes(payload[8:10], "little") & 0x3FFF
    return None, None


def _jpeg_dimensions(handle) -> tuple[int | None, int | None]:
    handle.seek(2)
    while True:
        marker = handle.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None, None
        length = struct.unpack(">H", handle.read(2))[0]
        if 0xC0 <= marker[1] <= 0xCF and marker[1] not in {0xC4, 0xC8, 0xCC}:
            handle.read(1)
            height, width = struct.unpack(">HH", handle.read(4))
            return int(width), int(height)
        handle.seek(length - 2, 1)


def _svg_dimensions(path: Path) -> tuple[int | None, int | None]:
    head = path.read_text(encoding="utf-8", errors="replace")[:2048]
    viewbox = re.search(r'viewBox\s*=\s*["\']\s*[\d.-]+\s+[\d.-]+\s+([\d.]+)\s+([\d.]+)', head, re.I)
    if viewbox:
        return int(float(viewbox.group(1))), int(float(viewbox.group(2)))
    width = re.search(r'\bwidth\s*=\s*["\'](\d+)', head)
    height = re.search(r'\bheight\s*=\s*["\'](\d+)', head)
    return (int(width.group(1)), int(height.group(1))) if width and height else (None, None)


def _usable_as(width: int | None, height: int | None) -> list[str]:
    if not width or not height:
        return []
    aspect = width / height
    roles: list[str] = []
    if width >= 1200 and aspect >= 1.4:
        roles.append("hero")
    if width >= 600:
        roles.append("inline")
    if 0.7 <= aspect <= 1.45:
        roles.append("tile")
    if width <= 320:
        roles.append("thumbnail")
    if width <= 128 and 0.8 <= aspect <= 1.25:
        roles.append("icon")
    return roles or ["inline"]


def build_asset_infos(bundle: InputBundle) -> list[AssetInfo]:
    """Describe every delivered asset with facts read straight off the file."""
    infos: list[AssetInfo] = []
    for item in bundle.files:
        output = bundle.asset_map.get(item.relative.lower())
        if output is None:
            continue
        width = height = None
        if item.kind == "image":
            width, height = image_dimensions(item.path)
        infos.append(AssetInfo(
            path=output,
            kind=item.kind,
            bytes=item.bytes,
            width=width,
            height=height,
            aspect=round(width / height, 2) if width and height else None,
            usable_as=_usable_as(width, height),
        ))
    logger.info("Asset inventory built: count=%d", len(infos))
    return infos
