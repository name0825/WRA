from html.parser import HTMLParser
from urllib.parse import urlparse

from wra.core.models import FeatureModel


class _ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str | None]] = []
        self.images: list[tuple[str | None, str | None]] = []
        self.videos: list[tuple[str | None, str | None]] = []
        self.buttons: list[tuple[str, str | None]] = []
        self._button_depth = 0
        self._button_text: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.text_parts.append(text)
            if self._button_depth:
                self._button_text.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            self.links.append(("", values.get("href")))
        elif tag == "img":
            self.images.append((values.get("src"), values.get("alt")))
        elif tag == "video":
            self.videos.append((values.get("src"), values.get("poster")))
        elif tag == "button":
            self._button_depth = 1
            self._button_text = []
            self.buttons.append(("", values.get("type")))

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._button_depth:
            text = " ".join(self._button_text).strip()
            href = self.buttons[-1][1] if self.buttons else None
            if self.buttons:
                self.buttons[-1] = (text, href)
            self._button_depth = 0
            self._button_text = []


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _href_is_present(href: str | None) -> bool:
    return bool(href and href.strip())


def _href_is_malformed(href: str) -> bool:
    value = href.strip()
    if not value:
        return True
    if value.startswith(("#", "/", "./", "../")):
        return False
    if value.startswith(("mailto:", "tel:", "javascript:")):
        return False
    parsed = urlparse(value)
    return not bool(parsed.scheme and (parsed.netloc or parsed.scheme in {"file", "data"}))


def validate_content(html: str, features: FeatureModel) -> tuple[list[str], list[str]]:
    parser = _ContentParser()
    parser.feed(html)
    parser.close()

    errors: list[str] = []
    warnings: list[str] = []
    page_text = _normalise(" ".join(parser.text_parts))

    for required in features.requirements.must_preserve:
        value = _normalise(required)
        if value and value not in page_text:
            errors.append(f"missing required content: {required}")

    for link in features.content.links:
        if not _href_is_present(link.href):
            errors.append(f"missing required link href: {link.text}")
        elif _href_is_malformed(link.href or ""):
            errors.append(f"malformed required link href: {link.href}")

    for button in features.content.buttons:
        if not _href_is_present(button.href):
            errors.append(f"missing required CTA href: {button.text}")
        elif _href_is_malformed(button.href or ""):
            errors.append(f"malformed required CTA href: {button.href}")

    generated_links = {href for _, href in parser.links if _href_is_present(href)}
    generated_buttons = {href for _, href in parser.buttons if _href_is_present(href)}
    for link in features.content.links:
        if _href_is_present(link.href) and link.href not in generated_links:
            errors.append(f"missing required link: {link.href}")
    for button in features.content.buttons:
        if _href_is_present(button.href) and button.href not in generated_links | generated_buttons:
            errors.append(f"missing required CTA link: {button.href}")

    generated_images = {src for src, _ in parser.images if _href_is_present(src)}
    for image in features.media.images:
        if _href_is_present(image.src) and image.src not in generated_images:
            errors.append(f"missing required image: {image.src}")

    for src, alt in parser.images:
        if _href_is_present(src) and not (alt and alt.strip()):
            warnings.append(f"image missing alt: {src}")

    generated_videos = {src for src, _ in parser.videos if _href_is_present(src)}
    for video in features.media.videos:
        if _href_is_present(video.src) and video.src not in generated_videos:
            errors.append(f"missing required video: {video.src}")

    return errors, warnings
