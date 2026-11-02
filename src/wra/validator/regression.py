import re
import unicodedata
from html import unescape
from urllib.parse import unquote

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import FeatureModel
from wra.validator.claims import find_meta_notes
from wra.validator.facts import find_missing_business_facts
from wra.validator.tokens import all_source_text, extract_visible_text
from wra.validator.visual import _references_image, _source_image_basenames

PREFIX = "regression:"

_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_BRACKET_PLACEHOLDER_RE = re.compile(r"\[\s*[A-Za-z][A-Za-z0-9 _\-]{2,38}\]")
_KEY_LINK_SCHEMES = ("mailto:", "tel:", "http://", "https://")


def _normalize_href(href: str) -> str:
    href = unquote(unescape(href.strip()))
    lowered = href.lower()
    if lowered.startswith("tel:"):
        number = href[4:].strip()
        return "tel:" + ("+" if number.startswith("+") else "") + re.sub(r"\D", "", number)
    if lowered.startswith("mailto:"):
        return "mailto:" + href[7:].split("?", 1)[0].strip().lower()
    origin = re.match(r"https?://[^/?#]*", href, re.IGNORECASE)
    if origin:
        href = origin.group(0).lower() + href[origin.end():]
    return href.rstrip("/")


def _source_key_links(features: FeatureModel) -> list[str]:
    hrefs = [item.href for item in (*features.content.links, *features.content.buttons) if item.href]
    return list(dict.fromkeys(href.strip() for href in hrefs if href.strip().lower().startswith(_KEY_LINK_SCHEMES)))


def _visible(html: str) -> str:
    return unicodedata.normalize("NFKC", extract_visible_text(html))


def _placeholders(html: str) -> set[str]:
    text = _visible(html)
    found = {match.group(0) for match in _BRACKET_PLACEHOLDER_RE.finditer(text)}
    found.update(note.split(": ", 1)[1] for note in find_meta_notes(text))
    return found


def _viewports(browser: BrowserEvaluationResult | None) -> dict | None:
    if browser is None or not browser.enabled:
        return None
    return {viewport.name: viewport for viewport in browser.viewports}


def find_regressions(base_html: str, base_browser: BrowserEvaluationResult | None, html: str, browser: BrowserEvaluationResult | None, features: FeatureModel, check_brief_facts: bool = False) -> list[str]:
    """What a candidate lost or broke that the page it was derived from did not."""
    found: list[str] = []

    if check_brief_facts:
        before = set(find_missing_business_facts(base_html, features))
        found += [f"{PREFIX} {item}" for item in find_missing_business_facts(html, features) if item not in before]

    base_hrefs = {_normalize_href(href) for href in _HREF_RE.findall(base_html)}
    hrefs = {_normalize_href(href) for href in _HREF_RE.findall(html)}
    for link in _source_key_links(features):
        key = _normalize_href(link)
        if key in base_hrefs and key not in hrefs:
            found.append(f"{PREFIX} source link no longer on the page: {link}")

    source_text = unicodedata.normalize("NFKC", all_source_text(features)).casefold()
    for item in sorted(_placeholders(html) - _placeholders(base_html)):
        label = item[1:-1].strip() if item.startswith("[") else item
        if item.casefold() not in source_text and not re.search(rf"\b{re.escape(label.casefold())}\b", source_text):
            found.append(f"{PREFIX} placeholder text added: {item}")

    names = _source_image_basenames(features)
    if any(_references_image(base_html, name) for name in names) and not any(_references_image(html, name) for name in names):
        found.append(f"{PREFIX} every source image that the page used was dropped")

    base_viewports, viewports = _viewports(base_browser), _viewports(browser)
    if base_viewports is not None and viewports is not None:
        base_broken = {src for viewport in base_viewports.values() for src in viewport.broken_images}
        new_broken = list(dict.fromkeys(src for viewport in viewports.values() for src in viewport.broken_images if src not in base_broken))
        if new_broken:
            found.append(f"{PREFIX} new broken image(s): " + ", ".join(new_broken[:3]))
        for name, viewport in viewports.items():
            was = base_viewports.get(name)
            if was is not None and viewport.scroll_width - viewport.viewport_width > 1 and not was.scroll_width - was.viewport_width > 1:
                found.append(f"{PREFIX} new horizontal overflow at {name} {viewport.width}px")
        base_errors = {error for viewport in base_viewports.values() for error in viewport.page_errors}
        new_errors = list(dict.fromkeys(error for viewport in viewports.values() for error in viewport.page_errors if error not in base_errors))
        if new_errors:
            found.append(f"{PREFIX} new script error(s): " + "; ".join(new_errors[:3]))

    return found
