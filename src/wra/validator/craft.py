import colorsys
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from wra.core.models import FeatureModel
from wra.validator.tokens import all_source_text

_MAX_PER_RULE = 5
_DETAIL_LIMIT = 120
_HEX_SPRAWL_LIMIT = 12
_BLUR_LIMIT_PX = 8.0

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
_RGB_RE = re.compile(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})")
_BLUR_RE = re.compile(r"blur\(\s*(\d+(?:\.\d+)?|\.\d+)(px|rem)\s*\)", re.IGNORECASE)
_GRADIENT_START_RE = re.compile(r"(?:linear|radial)-gradient\(", re.IGNORECASE)
_OUTLINE_OFF_RE = re.compile(r"^(?:none|0)(?:\s|!|$)")
_HSL_RE = re.compile(
    r"hsla?\(\s*(\d+(?:\.\d+)?|\.\d+)(?:deg)?\s*[, ]\s*(\d+(?:\.\d+)?|\.\d+)%\s*[, ]\s*(\d+(?:\.\d+)?|\.\d+)%"
)
_URL_RE = re.compile(r"url\([^)]*\)", re.IGNORECASE)
_COMPOUND_TOKEN_RE = re.compile(r"[.#]?[\w-]+")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF⭐"
    "☀☁☔☕☎☑♈-♓♿⚓⚠⚡⚪⚫⚽⚾"
    "⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽"
    "✂✅✈✉✊✋✏✨❤❌❎❓-❕❗➕-➗➰➿]"
)
_PLACEHOLDER_RE = re.compile(r"lorem ipsum|john doe|jane doe|\bAcme\b|your company", re.IGNORECASE)
_MAX_SCALE_RE = re.compile(r"maximum-scale\s*=\s*([\d.]+)")
_USER_SCALABLE_RE = re.compile(r"user-scalable\s*=\s*(\w+)")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_TAILWIND_VIOLETS = frozenset({
    "#6366f1", "#4f46e5", "#4338ca", "#818cf8", "#8b5cf6",
    "#7c3aed", "#6d28d9", "#a78bfa", "#a855f7", "#9333ea",
})
_TEXT_TAGS = frozenset({"a", "button", "h1", "h2", "h3", "h4", "h5", "h6"})
_CLICKABLE_TAGS = frozenset({"div", "span", "li"})
_NATIVE_CONTROLS = frozenset({"a", "button", "input", "summary"})


@dataclass(frozen=True)
class CraftIssue:
    rule: str
    category: str
    detail: str


class _CraftParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.css_chunks: list[str] = []
        self.inline_styles: list[str] = []
        self.text_nodes: list[str] = []
        self.element_texts: list[tuple[str, str]] = []
        self.imgs: list[dict[str, str | None]] = []
        self.viewports: list[str] = []
        self.clickables: list[str] = []
        self._skip: str | None = None
        self._stack: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        style = attr.get("style")
        if style:
            self.inline_styles.append(style)
        if tag in ("script", "style"):
            self._skip = tag
        elif tag == "img":
            self.imgs.append(attr)
        elif tag == "meta" and (attr.get("name") or "").lower() == "viewport":
            self.viewports.append(attr.get("content") or "")
        if tag in _CLICKABLE_TAGS and attr.get("onclick") is not None:
            self.clickables.append(f"<{tag}> with onclick")
        elif tag not in _NATIVE_CONTROLS and (attr.get("role") or "").lower() == "button" and "tabindex" not in attr:
            self.clickables.append(f'<{tag} role="button"> without tabindex')
        if tag in _TEXT_TAGS:
            self._stack.append((tag, []))

    def handle_endtag(self, tag: str) -> None:
        if tag == self._skip:
            self._skip = None
        if tag not in _TEXT_TAGS:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                while len(self._stack) > index:
                    self._finalize(*self._stack.pop())
                return

    def handle_data(self, data: str) -> None:
        if self._skip == "style":
            self.css_chunks.append(data)
            return
        if self._skip:
            return
        self.text_nodes.append(data)
        for _, parts in self._stack:
            parts.append(data)

    def close(self) -> None:
        super().close()
        while self._stack:
            self._finalize(*self._stack.pop())

    def _finalize(self, tag: str, parts: list[str]) -> None:
        self.element_texts.append((tag, " ".join("".join(parts).split())))


def _split_declarations(body: str) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    depth = 0
    start = 0
    pieces: list[str] = []
    for index, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            pieces.append(body[start:index])
            start = index + 1
    pieces.append(body[start:])
    for piece in pieces:
        prop, sep, value = piece.partition(":")
        if sep and prop.strip():
            declarations.append((prop.strip().lower(), value.strip()))
    return declarations


def _innermost_rules(css: str) -> list[tuple[str, str]]:
    """Return (selector, body) for every block that contains no nested block."""
    rules: list[tuple[str, str]] = []
    stack: list[list] = []
    start = 0
    for index, char in enumerate(css):
        if char == "{":
            if stack:
                stack[-1][2] = True
            selector = css[start:index].rsplit(";", 1)[-1].strip()
            stack.append([selector, index + 1, False])
            start = index + 1
        elif char == "}":
            if stack:
                selector, body_start, has_child = stack.pop()
                if not has_child:
                    rules.append((selector, css[body_start:index]))
            start = index + 1
    return rules


def _normalize_hex(literal: str) -> str:
    digits = literal[1:].lower()
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits[:3])
    return "#" + digits[:6]


def _hex_literals(text: str) -> list[str]:
    return [_normalize_hex(match) for match in _HEX_RE.findall(_URL_RE.sub("", text))]


def _source_palette(features: FeatureModel | None) -> set[str]:
    if features is None or features.source_assets is None:
        return set()
    palette: set[str] = set()
    for entry in features.source_assets.styles.palette:
        palette.update(_colors_in(entry))
    return palette


def _colors_in(text: str) -> list[str]:
    colors = _hex_literals(text)
    for r, g, b in _RGB_RE.findall(text):
        colors.append("#{:02x}{:02x}{:02x}".format(min(int(r), 255), min(int(g), 255), min(int(b), 255)))
    for h, s, l in _HSL_RE.findall(text):
        try:
            r, g, b = colorsys.hls_to_rgb((float(h) % 360) / 360, min(float(l), 100) / 100, min(float(s), 100) / 100)
        except ValueError:
            continue
        colors.append("#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255)))
    return colors


def _img_is_sized_by_css(attr: dict[str, str | None], rules: list[tuple[str, str]]) -> bool:
    classes = {f".{name}" for name in (attr.get("class") or "").split()}
    element_id = attr.get("id")
    own = {f"#{element_id}"} if element_id else set()
    bodies = [attr.get("style") or ""]
    for selector, body in rules:
        for part in selector.split(","):
            compound = re.split(r"[\s>+~]+", part.strip())[-1]
            compound = re.sub(r"::?[\w-]+(?:\([^)]*\))?|\[[^\]]*\]", "", compound)
            tokens = _COMPOUND_TOKEN_RE.findall(compound)
            if tokens and (tokens[0] == "img" or classes & set(tokens) or own & set(tokens)):
                bodies.append(body)
                break
    for body in bodies:
        for prop, value in _split_declarations(body):
            lowered = value.lower().strip()
            if prop == "height" and not lowered.startswith("auto"):
                return True
            if prop == "position" and lowered.startswith(("absolute", "fixed")):
                return True
    return False


def _hue_and_saturation(hex_color: str) -> tuple[float, float]:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, _, saturation = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, saturation


def _gradients(value: str) -> list[str]:
    found: list[str] = []
    for match in _GRADIENT_START_RE.finditer(value):
        depth = 1
        end = match.end()
        while end < len(value) and depth:
            if value[end] == "(":
                depth += 1
            elif value[end] == ")":
                depth -= 1
            end += 1
        found.append(value[match.start():end])
    return found


def _shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT - 3] + "..."


def _to_float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return float("inf")


def find_craft_issues(html: str, features: FeatureModel | None = None) -> list[CraftIssue]:
    parser = _CraftParser()
    parser.feed(html)
    parser.close()

    source_text = all_source_text(features) if features is not None else ""
    source_lower = source_text.lower()
    palette = _source_palette(features)

    css_text = _CSS_COMMENT_RE.sub("", "\n".join(parser.css_chunks))
    rules = _innermost_rules(css_text) + [("", style) for style in parser.inline_styles]
    declarations = [(selector, prop, value) for selector, body in rules for prop, value in _split_declarations(body)]
    has_focus_visible = ":focus-visible" in css_text.lower() or any(
        ":focus-visible" in style.lower() for style in parser.inline_styles
    )
    has_aspect_ratio = any(prop == "aspect-ratio" for _, prop, _ in declarations)

    issues: list[CraftIssue] = []
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}

    def add(rule: str, category: str, detail: str) -> None:
        detail = _shorten(detail)
        if (rule, detail) in seen or counts.get(rule, 0) >= _MAX_PER_RULE:
            return
        seen.add((rule, detail))
        counts[rule] = counts.get(rule, 0) + 1
        issues.append(CraftIssue(rule, category, detail))

    for _, prop, value in declarations:
        lowered = value.lower()
        if prop in ("transition", "transition-property") and lowered.startswith("all"):
            add("transition-all", "quality", f"{prop}: {value}")
        if prop == "outline" and not has_focus_visible and _OUTLINE_OFF_RE.match(lowered):
            add("outline-removed", "quality", f"outline: {value}")
        if prop in ("filter", "backdrop-filter", "-webkit-backdrop-filter"):
            for number, unit in _BLUR_RE.findall(value):
                if float(number) * (16 if unit.lower() == "rem" else 1) > _BLUR_LIMIT_PX:
                    add("heavy-blur", "ai_tell", f"{prop}: {value}")
                    break
        for literal in _hex_literals(value):
            if literal in _TAILWIND_VIOLETS and literal not in palette:
                add("tailwind-violet", "ai_tell", literal)
        for gradient in _gradients(value):
            colors = [(color, *_hue_and_saturation(color)) for color in _colors_in(gradient)]
            purples = [c for c, hue, sat in colors if 260 <= hue <= 300 and sat >= 0.3]
            blues = [c for c, hue, sat in colors if 190 <= hue <= 250 and sat >= 0.3]
            if purples and blues and not all(c in palette for c in purples):
                add("purple-blue-gradient", "ai_tell", gradient)

    for content in parser.viewports:
        lowered = content.lower()
        scalable = _USER_SCALABLE_RE.search(lowered)
        max_scale = _MAX_SCALE_RE.search(lowered)
        if (scalable and scalable.group(1) in ("no", "0")) or (max_scale and _to_float(max_scale.group(1)) <= 1):
            add("zoom-disabled", "quality", f"viewport: {content}")

    for attr in parser.imgs:
        style = (attr.get("style") or "").lower()
        if (
            (attr.get("width") is None or attr.get("height") is None)
            and "aspect-ratio" not in style
            and not has_aspect_ratio
            and not _img_is_sized_by_css(attr, rules)
        ):
            add("img-no-dimensions", "quality", f"<img src=\"{(attr.get('src') or '')[:80]}\">")

    for detail in parser.clickables:
        add("clickable-non-control", "quality", detail)

    for tag, text in parser.element_texts:
        for emoji in _EMOJI_RE.findall(text):
            if emoji not in source_text:
                add("emoji-icon", "quality", f"{emoji} in <{tag}>: {text}")
        if tag in ("a", "button") and text.endswith(("→", "->")) and text not in source_text:
            add("arrow-suffix", "ai_tell", f"<{tag}> {text}")

    for node in parser.text_nodes:
        for match in _PLACEHOLDER_RE.finditer(node):
            hit = match.group(0)
            if hit.lower() == "acme" and hit != "Acme":
                continue
            if hit.lower() not in source_lower:
                add("placeholder-copy", "ai_tell", hit)
        stripped = " ".join(node.split())
        if stripped and node.count("·") + node.count(" • ") >= 2 and stripped not in source_text:
            add("middot-meta", "ai_tell", stripped)

    sprawl = {
        literal
        for selector, prop, value in declarations
        if ":root" not in selector
        for literal in _hex_literals(value)
    } - palette
    if len(sprawl) > _HEX_SPRAWL_LIMIT:
        add("hex-sprawl", "ai_tell", f"{len(sprawl)} distinct hex colors outside :root")

    return issues


REFINABLE_CRAFT_RULES = ("transition-all", "outline-removed", "zoom-disabled")
_REFINABLE_CRAFT_FIXES = {
    "transition-all": "list only the properties that change instead of `all` (e.g. `transition: color .2s, background-color .2s, transform .2s`)",
    "outline-removed": "the page removes the focus outline and has no `:focus-visible` style; add a visible `:focus-visible` outline (e.g. `outline: 2px solid` in the accent color, `outline-offset: 2px`) for links, buttons, and form fields",
    "zoom-disabled": "remove `user-scalable=no` and any `maximum-scale` from the viewport meta so readers can zoom",
}


def find_refinable_craft_defects(html: str, features: FeatureModel | None = None) -> list[str]:
    return [
        f"{issue.detail}: {_REFINABLE_CRAFT_FIXES[issue.rule]}"
        for issue in find_craft_issues(html, features)
        if issue.rule in REFINABLE_CRAFT_RULES
    ]
