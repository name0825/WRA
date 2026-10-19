import logging
import re
from html.parser import HTMLParser
from urllib.parse import unquote

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import FeatureModel
from wra.validator.syntax import find_css_truncation

logger = logging.getLogger(__name__)

UNSTYLED_PREFIX = "Unstyled:"
IMAGES_DROPPED_PREFIX = "Images dropped:"
IMAGES_MISSING_PREFIX = "Images missing:"
LOW_CONTRAST_PREFIX = "Low contrast:"
BROKEN_IMAGE_PREFIX = "Broken image:"
EXPOSED_MARKUP_PREFIX = "Exposed markup:"
OVERFLOW_PREFIX = "Horizontal overflow:"
RENDER_COLLAPSE_PREFIX = "Render collapse:"
VERTICAL_GAP_PREFIX = "Vertical gap:"
VERTICAL_GAP_MIN_VIEWPORT_RATIO = 0.6
OVERSIZED_HEADER_LOGO_PREFIX = "Oversized header image:"
EDGE_HUGGING_TEXT_PREFIX = "Text at viewport edge:"
HEADER_OVERLAP_PREFIX = "Header overlap:"
OCCLUDED_TEXT_PREFIX = "Occluded text:"
WIDE_TEXT_WIDTH_PREFIX = "Narrow text column:"
_WIDE_TEXT_WIDTH_THRESHOLD = 600
_SEVERE_CONTRAST_RATIO = 3.0
_ICON_NAME_TOKENS = {"icon", "icons", "ico", "favicon", "sprite", "sprites"}
# Desktop viewports only: a normally laid-out page's first screen uses most
# of the viewport width; a collapsed multi-column layout that pushes
# everything but a narrow sidebar below the fold falls well short of it.
_MIN_DESKTOP_WIDTH = 1200
_FIRST_SCREEN_RIGHT_RATIO_THRESHOLD = 0.55
_EXCLUDED_TEXT_TAGS = {"script", "style", "code", "pre", "textarea", "kbd", "samp", "template", "noscript"}
# A leaked closing/self-closing tag, an attribute assignment leaked as text
# (`class="`), or a CSS declaration followed by a stray closing quote -- all
# shapes an LLM's markup can leak into the text it renders.
_LEAKED_TAG_CLOSE_RE = re.compile(r'"\s*/?>')
_LEAKED_ATTR_ASSIGN_RE = re.compile(r'\b(?:class|style|href|src|alt|id)\s*=\s*"')
_LEAKED_CSS_DECL_RE = re.compile(r'[a-zA-Z-]{2,20}\s*:\s*[#0-9a-zA-Z.%\- ]{1,30}"')


class _StyleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_stylesheet_link = False
        self.has_style_content = False
        self.inline_declarations = 0
        self.inline_styled_elements = 0
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        declarations = sum(1 for part in (attr_dict.get("style") or "").split(";") if ":" in part and all(side.strip() for side in part.split(":", 1)))
        self.inline_declarations += declarations
        if declarations:
            self.inline_styled_elements += 1
        if tag == "link":
            rel = (attr_dict.get("rel") or "").lower().split()
            if "stylesheet" in rel:
                self.has_stylesheet_link = True
        elif tag == "style":
            self._in_style = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style and data.strip():
            self.has_style_content = True


MIN_INLINE_DECLARATIONS = 3
MIN_INLINE_STYLED_ELEMENTS = 2


def _is_unstyled(html: str, browser_result: BrowserEvaluationResult | None) -> bool:
    parser = _StyleParser()
    parser.feed(html)
    has_inline_styling = parser.inline_declarations >= MIN_INLINE_DECLARATIONS and parser.inline_styled_elements >= MIN_INLINE_STYLED_ELEMENTS
    if browser_result is not None and browser_result.enabled and browser_result.viewports:
        counts = [
            viewport.stylesheet_rule_count
            for viewport in browser_result.viewports
            if viewport.stylesheet_rule_count is not None
        ]
        if counts:
            return all(count == 0 for count in counts) and not has_inline_styling
    return not (parser.has_style_content or parser.has_stylesheet_link or has_inline_styling)


def _source_image_basenames(features: FeatureModel) -> list[str]:
    basenames = []
    for image in features.media.images:
        src = image.src
        if not src or src.startswith("data:"):
            continue
        basenames.append(src.rsplit("/", 1)[-1])
    return basenames


def _references_image(html: str, name: str) -> bool:
    haystacks = {html, unquote(html)}
    for candidate in {name, unquote(name)}:
        pattern = rf"(?<![A-Za-z0-9_.\-]){re.escape(candidate)}(?![A-Za-z0-9_\-]|\.[A-Za-z0-9])"
        if any(re.search(pattern, haystack) for haystack in haystacks):
            return True
    return False


def _is_content_image(src: str) -> bool:
    tokens = set(re.split(r"[^a-z]+", src.rsplit("/", 1)[-1].lower()))
    if "logo" in tokens or tokens & _ICON_NAME_TOKENS:
        return False
    return True


def find_missing_content_images(html: str, features: FeatureModel) -> list[str]:
    """Report provided content images the page does not use.

    The logo and decorative icons (recognised by name, since the image model
    records no size) are excluded. `find_visual_defects` only notices when every
    image is gone, which a single kept logo hides; this counts the content
    images on their own.
    """
    content = list(
        dict.fromkeys(
            image.src.rsplit("/", 1)[-1]
            for image in features.media.images
            if image.src and not image.src.startswith("data:") and _is_content_image(image.src)
        )
    )
    missing = [name for name in content if not _references_image(html, name)]
    if not missing:
        return []
    return [f"{IMAGES_MISSING_PREFIX} {len(missing)} of {len(content)} provided content image(s) are not used in the generated HTML: {', '.join(missing)}"]


def find_contrast_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report rendered text whose measured WCAG contrast makes it hard to read.

    Reported only when at least one distinct text falls below 3.0:1 (hard to
    read at any size); a mild WCAG AA miss above that is noise. The same text
    failing at several viewports counts once.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    failures = {}
    for viewport in browser_result.viewports:
        for failure in viewport.contrast_failures:
            failures.setdefault((failure.foreground, failure.background, failure.text), failure)
    worst = sorted(failures.values(), key=lambda failure: failure.ratio)
    if not worst or worst[0].ratio >= _SEVERE_CONTRAST_RATIO:
        return []
    examples = "; ".join(f'"{failure.text}" {failure.ratio:.2f}:1 ({failure.foreground} on {failure.background})' for failure in worst[:3])
    return [f"{LOW_CONTRAST_PREFIX} {len(worst)} rendered text element(s) fall below the WCAG contrast minimum, e.g. {examples}"]


class _BrokenImgParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.broken: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        malformed_name = any('"' in name or "'" in name for name, _ in attrs)
        attr_dict = dict(attrs)
        src = attr_dict.get("src")
        missing_src = src is None or not src.strip()
        has_fallback = bool((attr_dict.get("srcset") or "").strip() or (attr_dict.get("data-src") or "").strip())
        if malformed_name or (missing_src and not has_fallback):
            raw = (self.get_starttag_text() or "<img>").strip()
            self.broken.append(raw)


def find_broken_image_markup(html: str) -> list[str]:
    """Report `<img>` tags with no usable source: a missing `src`, or a malformed attribute name.

    An unterminated `src` value (`src/assets/x.png" alt="...`) makes the HTML
    parser split the rest of the tag into bogus attributes -- markup a plain
    read misses but the browser renders as a broken image.
    """
    parser = _BrokenImgParser()
    parser.feed(html)
    return parser.broken


def find_broken_images(html: str, browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Combine statically malformed `<img>` markup with images that failed to load in the browser."""
    broken: dict[str, None] = dict.fromkeys(find_broken_image_markup(html))
    if browser_result is not None and browser_result.enabled:
        for viewport in browser_result.viewports:
            for src in viewport.broken_images:
                broken.setdefault(src, None)

    broken_list = list(broken)
    if not broken_list:
        return []
    examples = ", ".join(f'"{item}"' for item in broken_list[:3])
    return [f"{BROKEN_IMAGE_PREFIX} {len(broken_list)} broken image(s) detected, e.g. {examples}"]


def find_overflow_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report viewports whose page is wider than the viewport itself.

    Reported per viewport once the page is more than 1px wider than the
    viewport (sub-pixel rounding is noise). Names up to 3 offending elements
    per viewport so the regenerating model knows what to fix, not just that
    something overflows.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    lines = []
    for viewport in browser_result.viewports:
        overflow_px = viewport.scroll_width - viewport.viewport_width
        if overflow_px <= 1:
            continue
        line = f"{viewport.name} {viewport.width}px: page is {viewport.scroll_width}px wide (+{overflow_px}px)"
        offenders = ", ".join(element.selector for element in viewport.overflow_elements[:3])
        if offenders:
            line += f", caused by {offenders}"
        lines.append(line)
    if not lines:
        return []
    return [f"{OVERFLOW_PREFIX} " + "; ".join(lines)]


class _ExposedMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _EXCLUDED_TEXT_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _EXCLUDED_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if _LEAKED_TAG_CLOSE_RE.search(text) or _LEAKED_ATTR_ASSIGN_RE.search(text) or _LEAKED_CSS_DECL_RE.search(text):
            self.hits.append(text)


def find_exposed_markup(html: str) -> list[str]:
    """Report visible text that leaked a markup or CSS fragment instead of rendering as a tag/style.

    A generation glitch that drops a `<` or misplaces a quote turns markup
    into plain text the browser renders verbatim -- invisible to a
    page-structure read since the tag it should have been never appears.
    """
    parser = _ExposedMarkupParser()
    parser.feed(html)
    if not parser.hits:
        return []
    seen: list[str] = []
    for hit in parser.hits:
        if hit not in seen:
            seen.append(hit)
    examples = "; ".join(f'"{hit[:60]}"' for hit in seen[:3])
    return [f"{EXPOSED_MARKUP_PREFIX} {len(seen)} text node(s) show a raw HTML fragment as visible text (a localized leak, not a styling failure), e.g. {examples}"]


def find_render_collapse_defects(html: str, browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report a rendered page whose layout visibly collapsed: truncated CSS, a pushed-below-the-fold column, or squeezed text.

    Deterministic because the LLM evaluator judges the code it reads, not the
    screen it is shown, and a rotation whose rendering collapsed can still
    score well on markup and CSS alone.
    """
    findings = [f"{RENDER_COLLAPSE_PREFIX} {message}" for message in find_css_truncation(html)]
    if browser_result is None or not browser_result.enabled:
        return findings
    for viewport in browser_result.viewports:
        if viewport.width >= _MIN_DESKTOP_WIDTH and viewport.first_screen_max_right is not None and viewport.viewport_width:
            ratio = viewport.first_screen_max_right / viewport.viewport_width
            if ratio < _FIRST_SCREEN_RIGHT_RATIO_THRESHOLD:
                findings.append(
                    f"{RENDER_COLLAPSE_PREFIX} {viewport.name} {viewport.width}px: first-screen content reaches only "
                    f"{ratio:.0%} of the viewport width, the rest of the first screen appears empty"
                )
        wrapped = viewport.wrapped_text_elements
        stacked = [element for element in wrapped if element.line_count >= 3 and element.char_count / element.line_count <= 1.5]
        if stacked or len(wrapped) >= 2:
            examples = "; ".join(
                f'"{element.text}" ({element.line_count} lines for {element.char_count} chars)'
                for element in (stacked + [element for element in wrapped if element not in stacked])[:3]
            )
            findings.append(
                f"{RENDER_COLLAPSE_PREFIX} {viewport.name} {viewport.width}px: {len(wrapped)} "
                f"text element(s) wrap one or a few characters per line, e.g. {examples}"
            )
    if findings:
        logger.warning("Render collapse detected: %s", findings)
    return findings


def find_vertical_gap_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report a viewport with a blank vertical band at least 60% as tall as the viewport.

    Evidence only: a section or hero whose content failed to render still
    leaves the surrounding layout intact, so this is not folded into the
    render-collapse cap -- the LLM evaluator weighs it alongside what it sees.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    findings = []
    for viewport in browser_result.viewports:
        if not viewport.height:
            continue
        bands = sorted(
            (band for band in viewport.vertical_gap_bands if band.height >= viewport.height * VERTICAL_GAP_MIN_VIEWPORT_RATIO),
            key=lambda band: band.height,
            reverse=True,
        )[:3]
        if not bands:
            continue
        examples = "; ".join(f"{band.height}px starting at {band.top}px" for band in bands)
        findings.append(f"{VERTICAL_GAP_PREFIX} {viewport.name} {viewport.width}px: empty vertical band(s) at least 60% of the {viewport.height}px viewport height, e.g. {examples}")
    return findings


def find_oversized_header_logo_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report a header/nav logo image rendered far larger than a logo, e.g. covering the hero.

    Evidence only, not folded into any cap: a mis-sized logo is a concrete,
    fixable layout mistake distinct from the broader render-collapse signals.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    findings = []
    for viewport in browser_result.viewports:
        elements = viewport.header_logo_elements
        if not elements:
            continue
        examples = "; ".join(f"{element.selector} {element.width}x{element.height}px" for element in elements[:3])
        findings.append(f"{OVERSIZED_HEADER_LOGO_PREFIX} {viewport.name} {viewport.width}px: header/nav image(s) exceed a bounded logo size, e.g. {examples}")
    return findings


def find_edge_hugging_text_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report text runs whose rendered line box touches the viewport edge with no side padding.

    Evidence only, not folded into any cap: measured from actual glyph line boxes rather
    than element bounds, so a full-width block with centered text does not false-positive.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    findings = []
    for viewport in browser_result.viewports:
        elements = viewport.edge_hugging_text_elements
        if not elements:
            continue
        examples = "; ".join(f"{element.selector} in {element.section} @{element.left}-{element.right} '{element.text}'" for element in elements[:3])
        findings.append(f"{EDGE_HUGGING_TEXT_PREFIX} {viewport.name} {viewport.width}px: {len(elements)} text run(s) touch the viewport edge with no side padding, e.g. {examples}")
    return findings


def find_header_overlap_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report header/nav elements whose rendered boxes overlap each other.

    Evidence only, not folded into any cap: a logo or nav item rendered over another
    header element usually signals a positioning mistake rather than an intentional design.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    findings = []
    for viewport in browser_result.viewports:
        pairs = viewport.header_overlap_pairs
        if not pairs:
            continue
        examples = "; ".join(f"{pair.a} over {pair.b} ({pair.ratio:.0%})" for pair in pairs[:3])
        findings.append(f"{HEADER_OVERLAP_PREFIX} {viewport.name} {viewport.width}px: header items overlap each other, e.g. {examples}")
    return findings


def find_occluded_text_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report text elements whose rendered glyphs are covered by another element.

    Evidence only, not folded into any cap: fixed/sticky occluders count only at scroll 0,
    other occluders at any scroll position; either usually signals a positioning mistake.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    findings = []
    for viewport in browser_result.viewports:
        elements = viewport.occluded_text_elements
        if not elements:
            continue
        examples = "; ".join(f"{element.selector} under {element.occluder} '{element.text}'" for element in elements[:3])
        findings.append(f"{OCCLUDED_TEXT_PREFIX} {viewport.name} {viewport.width}px: text is covered by another element (a fixed/sticky occluder only at scroll 0), e.g. {examples}")
    return findings


def find_wide_text_width_defects(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report a narrow text column at a 1920px-wide viewport.

    Evidence only, not folded into any cap: the median width of each tall section's text and
    image content is a measured value that the evaluator weighs against the screenshots.
    """
    if browser_result is None or not browser_result.enabled:
        return []
    median = browser_result.wide_text_width_median
    if median is None or median >= _WIDE_TEXT_WIDTH_THRESHOLD:
        return []
    return [f"{WIDE_TEXT_WIDTH_PREFIX} at a 1920px-wide viewport the median width of the page's text and image content per section is {median}px, below {_WIDE_TEXT_WIDTH_THRESHOLD}px; on wide screens the body text sits in a narrow column. Measured value, not a verdict: weigh it with the screenshots."]


def find_visual_defects(
    html: str,
    features: FeatureModel,
    browser_result: BrowserEvaluationResult | None = None,
) -> list[str]:
    """Report visual defects an evaluator LLM tends to miss: no CSS, no source images, low contrast.

    A CSS-less page or a page whose source images were all dropped is a defect
    that plain page-structure reasoning can miss but a deterministic check cannot.
    """
    findings: list[str] = []
    if _is_unstyled(html, browser_result):
        findings.append(f"{UNSTYLED_PREFIX} the page has no applied CSS (no styled <style> content, no working stylesheet link)")

    basenames = _source_image_basenames(features)
    if basenames and not any(_references_image(html, name) for name in basenames):
        findings.append(f"{IMAGES_DROPPED_PREFIX} none of the {len(basenames)} source image(s) are referenced in the generated HTML")
    elif basenames:
        findings.extend(find_missing_content_images(html, features))

    findings.extend(find_contrast_defects(browser_result))
    findings.extend(find_broken_images(html, browser_result))
    findings.extend(find_exposed_markup(html))
    findings.extend(find_overflow_defects(browser_result))
    findings.extend(find_render_collapse_defects(html, browser_result))
    findings.extend(find_vertical_gap_defects(browser_result))
    findings.extend(find_oversized_header_logo_defects(browser_result))
    findings.extend(find_edge_hugging_text_defects(browser_result))
    findings.extend(find_header_overlap_defects(browser_result))
    findings.extend(find_occluded_text_defects(browser_result))
    findings.extend(find_wide_text_width_defects(browser_result))

    if findings:
        logger.warning("Visual defects detected: %s", findings)
    return findings
