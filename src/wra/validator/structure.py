from html.parser import HTMLParser


_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_REQUIRED_TAGS = {"html", "head", "body"}


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
