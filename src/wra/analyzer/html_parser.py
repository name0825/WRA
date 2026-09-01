from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class HTMLNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["HTMLNode"] = field(default_factory=list)
    text: str = ""

    def text_content(self) -> str:
        parts = [self.text]
        parts.extend(child.text_content() for child in self.children)
        return " ".join(part.strip() for part in parts if part.strip()).strip()


class WRAHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HTMLNode("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HTMLNode(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HTMLNode(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].text += data


def parse_html(html: str) -> HTMLNode:
    parser = WRAHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.root
