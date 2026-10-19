from html.parser import HTMLParser


_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_REQUIRED_TAGS = {"html", "head", "body"}

_P_CLOSING_TAGS = {
    "p", "div", "ul", "ol", "li", "table", "section", "article", "header",
    "footer", "nav", "aside", "main", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "form", "hr", "figure",
}
_IMPLICIT_CLOSE: dict[str, set[str]] = {
    "li": {"li"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"td", "th", "tr"},
    "option": {"option"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
}
for _tag in _P_CLOSING_TAGS:
    _IMPLICIT_CLOSE.setdefault(_tag, set()).add("p")
del _tag
_BOUNDARY_TAGS = {"ul", "ol", "table", "select", "dl"}


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.ids: set[str] = set()
        self.duplicate_ids: set[str] = set()
        self.seen_tags: set[str] = set()
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.seen_tags.add(tag)
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            if element_id in self.ids:
                self.duplicate_ids.add(element_id)
            self.ids.add(element_id)

        if tag not in _VOID_ELEMENTS:
            if tag in {"html", "head", "body"} and tag in self.stack:
                self.errors.append(f"duplicate required tag: {tag}")
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_ELEMENTS and self.stack and self.stack[-1] == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_ELEMENTS:
            return
        if not self.stack:
            self.errors.append(f"unexpected closing tag: {tag}")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            return
        if tag in self.stack:
            self.errors.append(f"mismatched closing tag: {tag}, expected {self.stack[-1]}")
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"unexpected closing tag: {tag}")


def validate_structure(html: str) -> tuple[list[str], list[str]]:
    parser = _StructureParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        return [f"HTML parser error: {exc}"], []

    errors = list(parser.errors)
    warnings: list[str] = []

    missing_tags = _REQUIRED_TAGS - parser.seen_tags
    errors.extend(f"missing required tag: <{tag}>" for tag in sorted(missing_tags))

    if parser.stack:
        errors.append(f"unclosed tag: <{parser.stack[-1]}>")

    errors.extend(f"duplicate id: {element_id}" for element_id in sorted(parser.duplicate_ids))

    if "main" not in parser.seen_tags:
        warnings.append("missing semantic <main> element")

    return errors, warnings


class _StructureRepairer(HTMLParser):
    """Rebuild a tag-balanced document from malformed HTML.

    Only tag structure is touched: unclosed tags are closed and stray or
    unmatched closing tags are dropped. Doctype, comments, text, and
    attribute values are re-emitted exactly as parsed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.parts: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        targets = _IMPLICIT_CLOSE.get(tag)
        if targets:
            while self.stack and self.stack[-1] in targets and self.stack[-1] not in _BOUNDARY_TAGS:
                self.parts.append(f"</{self.stack.pop()}>")
        self.parts.append(self.get_starttag_text())
        if tag not in _VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_ELEMENTS or not self.stack:
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            self.parts.append(f"</{tag}>")
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.parts.append(f"</{self.stack.pop()}>")
            self.stack.pop()
            self.parts.append(f"</{tag}>")

    def close(self) -> None:
        super().close()
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")


def repair_structure(html: str) -> str:
    """Repair unclosed and mismatched tags in `html` without touching content."""
    repairer = _StructureRepairer()
    repairer.feed(html)
    repairer.close()
    return "".join(repairer.parts)
