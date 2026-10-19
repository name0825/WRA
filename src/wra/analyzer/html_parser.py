import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser


# Elements whose end tag is optional in HTML5: a start tag listed here closes
# the open elements it maps to, so `<li>A<li>B` yields siblings, not nesting.
_P_CLOSING_TAGS = {"p", "div", "ul", "ol", "table", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "form", "pre", "hr", "nav", "main", "aside", "fieldset", "dl"}
_CLOSED_BY: dict[str, set[str]] = {tag: {"p"} for tag in _P_CLOSING_TAGS}
_CLOSED_BY.update({
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"tr", "td", "th"},
    "thead": {"thead", "tbody", "tfoot", "tr", "td", "th"},
    "tbody": {"thead", "tbody", "tfoot", "tr", "td", "th"},
    "tfoot": {"thead", "tbody", "tfoot", "tr", "td", "th"},
    "option": {"option"},
})
# Auto-closing never reaches past one of these, so an `li` inside a nested list
# cannot close the `li` that holds that list.
_SCOPE_BOUNDARY_TAGS = {"ul", "ol", "table", "tbody", "thead", "tfoot", "tr", "select", "dl", "form", "div", "body", "document"}
_TEXT_SEPARATING_TAGS = _P_CLOSING_TAGS | {
    "li", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "caption", "figure", "figcaption",
    "details", "summary", "address", "legend", "br", "option", "select", "textarea", "button", "label", "video", "audio", "img", "iframe",
}


@dataclass
class HTMLNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["HTMLNode"] = field(default_factory=list)
    text: str = ""
    # Text and child elements interleaved in document order. `text` and
    # `children` alone cannot express it, and reading a node's own text before
    # its children reorders any markup that puts a label in a child element:
    # `<span>2021.11</span> Third Camp` would read as `Third Camp 2021.11`.
    parts: list["str | HTMLNode"] = field(default_factory=list)

    def text_content(self) -> str:
        pieces: list[str] = []
        self._collect_text(pieces)
        return unicodedata.normalize("NFC", re.sub(r"\s+", " ", "".join(pieces))).strip()

    def _collect_text(self, pieces: list[str]) -> None:
        separator = self.tag in _TEXT_SEPARATING_TAGS
        if separator:
            pieces.append(" ")
        previous_was_element = False
        for part in self.parts:
            if isinstance(part, str):
                pieces.append(part)
                previous_was_element = False
            else:
                if previous_was_element:
                    pieces.append(" ")
                part._collect_text(pieces)
                previous_was_element = True
        if separator:
            pieces.append(" ")


class WRAHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HTMLNode("document")
        self._stack = [self.root]

    def _close_implied(self, tag: str) -> None:
        closes = _CLOSED_BY.get(tag)
        if not closes:
            return
        while True:
            for index in range(len(self._stack) - 1, 0, -1):
                name = self._stack[index].tag
                if name in closes:
                    del self._stack[index:]
                    break
                if name in _SCOPE_BOUNDARY_TAGS:
                    return
            else:
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._close_implied(tag)
        node = HTMLNode(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        self._stack[-1].parts.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._stack[-1].tag in {"script", "style"}:
            return
        self._stack[-1].text += data
        self._stack[-1].parts.append(data)


def parse_html(html: str) -> HTMLNode:
    parser = WRAHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.root
