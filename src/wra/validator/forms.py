import logging
from html.parser import HTMLParser

from wra.core.models import FeatureModel

logger = logging.getLogger(__name__)


class _FormCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.count += 1


def find_invented_forms(html: str, features: FeatureModel) -> list[str]:
    parser = _FormCounter()
    parser.feed(html)
    source = len(features.structure.forms)
    if parser.count <= source:
        return []
    logger.warning("Page has %d <form> element(s) but the source page had %d", parser.count, source)
    return [f"Page has {parser.count} <form> element(s) but the source page had {source}; remove the invented form and show the contact facts as mailto:/tel: links"]
