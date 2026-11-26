import re
from html import escape, unescape
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import unquote

from wra.core.models import BriefRecord, ContentFreedom, RecordSection
from wra.validator.brief_facts import _SPACED_DATE_RE, _attribute_text, _squash, _token_on_page, _tokens, _url_key
from wra.validator.dates import date_is_present, date_keys, without_dates
from wra.validator.tokens import extract_visible_text

RECORD_ATTR = "data-wra-record"
FIELD_ATTR = "data-wra-field"
SECTION_ATTR = "data-wra-section"

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_PROSE_KEYS = frozenset({"summary", "points", "body", "note", "description"})
_URL_KEYS = frozenset({"url", "github", "href", "link"})
_ANCHOR_KEYS = ("title", "name", "label", "text", "value")
_CONTAINERS = frozenset({"li", "article", "tr", "div", "section", "dl", "dd", "dt", "p"})
_SELF_SUFFICIENT = frozenset({"li", "tr", "article", "dd", "dt"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SECTION_TAGS = frozenset({"section", "article", "div", "aside"})
_TAG_RE = re.compile(r"<[^>]*>")
_HREF_RE = re.compile(r"""\bhref\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


class _Node:
    __slots__ = ("tag", "attrs", "start", "open_end", "close_start", "end", "parent", "children")

    def __init__(self, tag: str, attrs: dict, start: int, open_end: int, parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.start = start
        self.open_end = open_end
        self.close_start = open_end
        self.end = open_end
        self.parent = parent
        self.children: list[_Node] = []

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._html = html
        self._line_starts = [0] + [match.end() for match in re.finditer("\n", html)]
        self.root = _Node("#root", {}, 0, 0, None)
        self.root.close_start = self.root.end = len(html)
        self.nodes: list[_Node] = []
        self._open = [self.root]

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _add(self, tag, attrs) -> _Node:
        start = self._offset()
        text = self.get_starttag_text() or ""
        node = _Node(tag, {key: value or "" for key, value in attrs}, start, start + len(text), self._open[-1])
        self._open[-1].children.append(node)
        self.nodes.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, attrs)
        if tag not in _VOID:
            self._open.append(node)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        position = self._offset()
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                break
        else:
            return
        for node in self._open[index + 1:]:
            node.close_start = node.end = position
        matched = self._open[index]
        matched.close_start = position
        closing = self._html.find(">", position)
        matched.end = closing + 1 if closing >= 0 else len(self._html)
        del self._open[index:]

    def finish(self) -> None:
        for node in self._open[1:]:
            node.close_start = node.end = len(self._html)


class _View(NamedTuple):
    visible: str
    squashed: str
    raw: str


def _view(fragment: str) -> _View:
    visible = extract_visible_text(fragment) + " " + _attribute_text(fragment)
    return _View(_SPACED_DATE_RE.sub(r"\1.\2", visible), _squash(visible), unquote(unescape(fragment)).lower())


class _Doc:
    def __init__(self, html: str) -> None:
        self.html = html
        tree = _Tree(html)
        tree.feed(html)
        tree.close()
        tree.finish()
        self.root = tree.root
        self.nodes = tree.nodes
        self.page = _view(html)

    def fragment(self, node: _Node) -> str:
        return self.html[node.start:node.end]

    def inner(self, node: _Node) -> str:
        return self.html[node.open_end:node.close_start]

    def tagged(self, attribute: str) -> dict[str, list[_Node]]:
        found: dict[str, list[_Node]] = {}
        for node in self.nodes:
            if attribute in node.attrs:
                found.setdefault(node.attrs[attribute], []).append(node)
        return found

    def fields_of(self, record: _Node) -> dict[str, _Node]:
        fields: dict[str, _Node] = {}
        pending = list(record.children)
        while pending:
            node = pending.pop(0)
            if RECORD_ATTR in node.attrs:
                continue
            if FIELD_ATTR in node.attrs:
                fields.setdefault(node.attrs[FIELD_ATTR], node)
            pending[0:0] = node.children
        return fields

    def main(self) -> _Node | None:
        return next((node for node in self.nodes if node.tag == "main"), None)


def _attrs(node: _Node, drop: tuple[str, ...] = ("id", "href")) -> str:
    return "".join(f' {key}="{escape(value, quote=True)}"' for key, value in node.attrs.items() if key not in drop and not key.startswith("data-wra-"))


def _is_url(value: str) -> bool:
    return bool(_HTTP_RE.match(value))


def _is_fact(key: str, value: str) -> bool:
    return _is_url(value) or key.lower() not in _URL_KEYS


def _is_prose(key: str, freedom: ContentFreedom) -> bool:
    return key.lower() in _PROSE_KEYS and freedom != "low"


def _present(value: str, view: _View) -> bool:
    if _is_url(value):
        return _url_key(value) in view.raw
    keys = date_keys(value)
    if keys:
        found = date_keys(view.visible)
        remainder = _squash(re.sub(r"[\W_]+", "", without_dates(value)))
        return all(date_is_present(key, found) for key in keys) and remainder in view.squashed
    return _squash(value) in view.squashed


def _token_missing(value: str, view: _View) -> bool:
    tokens = _tokens(value)
    return bool(tokens) and not any(_token_on_page(token, view.squashed) for token in tokens)


def _items(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def _missing(key: str, value: str | list[str], freedom: ContentFreedom, view: _View, page: _View) -> list[str]:
    if _is_prose(key, freedom):
        return [item for item in _items(value) if _token_missing(item, page)]
    return [item for item in _items(value) if _is_fact(key, item) and not _present(item, view)]


def _field_markup(key: str, items: list[str], is_list: bool, style: _Node | None, record_tag: str) -> str:
    label = f' {FIELD_ATTR}="{escape(key, quote=True)}"'
    if not is_list:
        value = items[0]
        if _is_url(value):
            attrs = _attrs(style) if style is not None and style.tag == "a" else ""
            return f'<a{attrs}{label} href="{escape(value, quote=True)}">{escape(value)}</a>'
        style = None if style is not None and style.tag == "a" else style
        tag = style.tag if style is not None else "td" if record_tag == "tr" else "p" if key.lower() in _PROSE_KEYS else "span"
        attrs = _attrs(style) if style is not None else ""
        return f"<{tag}{attrs}{label}>{escape(value)}</{tag}>"
    outer = style.tag if style is not None else "ul"
    child = style.children[-1] if style is not None and style.children else None
    child_tag = child.tag if child is not None else "li" if outer in ("ul", "ol") else "span"
    child_attrs = _attrs(child) if child is not None else ""
    entries = "".join(f"<{child_tag}{child_attrs}>{_item_markup(item)}</{child_tag}>" for item in items)
    return f"<{outer}{_attrs(style) if style is not None else ''}{label}>{entries}</{outer}>"


def _item_markup(item: str) -> str:
    if _is_url(item):
        return f'<a href="{escape(item, quote=True)}">{escape(item)}</a>'
    return escape(item)


def _list_entries(items: list[str], list_node: _Node) -> str:
    child = list_node.children[-1] if list_node.children else None
    tag = child.tag if child is not None else "li" if list_node.tag in ("ul", "ol") else "span"
    attrs = _attrs(child) if child is not None else ""
    return "".join(f"<{tag}{attrs}>{_item_markup(item)}</{tag}>" for item in items)


def _record_markup(record: BriefRecord, styles: dict[str, _Node], wrapper: str, attrs: str) -> str:
    parts = []
    for key, value in record.fields.items():
        items = [item for item in _items(value) if _is_fact(key, item)]
        if items:
            parts.append(_field_markup(key, items, isinstance(value, list), styles.get(key), wrapper))
    return f'<{wrapper}{attrs} {RECORD_ATTR}="{escape(record.id, quote=True)}">{"".join(parts)}</{wrapper}>'


def _apply(html: str, edits: list[tuple[int, int, str]]) -> str:
    edits = [
        edit
        for edit in edits
        if not any(
            other[1] > other[0]
            and other[0] <= edit[0]
            and edit[1] <= other[1]
            and other[:2] != edit[:2]
            and not (edit[0] == edit[1] and edit[0] in other[:2])
            for other in edits
        )
    ]
    for start, end, text in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        html = html[:start] + text + html[end:]
    return html


def _strip_duplicate_ids(html: str, ids: set[str], repairs: list[str]) -> str:
    doc = _Doc(html)
    edits = []
    for record_id, nodes in doc.tagged(RECORD_ATTR).items():
        if record_id not in ids:
            continue
        for node in nodes[1:]:
            tag = html[node.start:node.open_end]
            stripped = re.sub(rf"\s+{RECORD_ATTR}\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", tag, count=1)
            edits.append((node.start, node.open_end, stripped))
            repairs.append(f"tagged: dropped duplicate record id {record_id}")
    return _apply(html, edits)


def _overwrite(doc: _Doc, element: _Node, value: str) -> tuple[int, int, str] | None:
    if element.tag in _VOID:
        return None
    if element.tag == "a" and _is_url(value):
        tag = doc.html[element.start:element.open_end]
        if _url_key(value) in unquote(unescape(tag)).lower():
            return None
        href = f'href="{escape(value, quote=True)}"'
        if _HREF_RE.search(tag):
            return element.start, element.open_end, _HREF_RE.sub(lambda _: href, tag, count=1)
        return element.start, element.open_end, tag[: 1 + len(element.tag)] + " " + href + tag[1 + len(element.tag):]
    if element.children or _squash(extract_visible_text(doc.inner(element))) == _squash(value):
        return None
    return element.open_end, element.close_start, escape(value)


def _section_styles(doc: _Doc, section: RecordSection, skip: str) -> dict[str, _Node]:
    tagged = doc.tagged(RECORD_ATTR)
    styles: dict[str, _Node] = {}
    for record in section.records:
        if record.id == skip:
            continue
        for node in tagged.get(record.id, [])[:1]:
            for key, field_node in doc.fields_of(node).items():
                styles.setdefault(key, field_node)
    return styles


def _locate(doc: _Doc, record: BriefRecord, freedom: ContentFreedom) -> _Node | None:
    anchor = next((_squash(value) for key in _ANCHOR_KEYS for value in [record.fields.get(key)] if isinstance(value, str) and _squash(value)), None)
    if anchor is None:
        return None
    scope = doc.main() or doc.root
    facts = [(key, value) for key, value in record.fields.items() if isinstance(value, str) and _is_fact(key, value) and not _is_prose(key, freedom)]
    best: tuple[int, int, _Node] | None = None
    for node in scope.descendants():
        if node.tag not in _CONTAINERS or RECORD_ATTR in node.attrs:
            continue
        if any(RECORD_ATTR in other.attrs for other in node.ancestors()) or any(RECORD_ATTR in other.attrs for other in node.descendants()):
            continue
        fragment = doc.fragment(node)
        if anchor not in _squash(unescape(_TAG_RE.sub("", fragment))):
            continue
        view = _view(fragment)
        if anchor not in view.squashed or (view.squashed == anchor and node.tag not in _SELF_SUFFICIENT):
            continue
        score = sum(1 for _, value in facts if _present(value, view))
        candidate = (-score, node.end - node.start, node)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return best[2] if best else None


def _find_section(doc: _Doc, section: RecordSection) -> _Node | None:
    heading = _squash(section.heading)
    for node in doc.nodes:
        if node.attrs.get(SECTION_ATTR) == section.id or node.attrs.get("id") == section.id:
            return node
    if not heading:
        return None
    for node in doc.nodes:
        if node.tag in _HEADINGS and _squash(extract_visible_text(doc.inner(node))) == heading:
            return next((ancestor for ancestor in node.ancestors() if ancestor.tag in _SECTION_TAGS), None)
    return None


def _new_section_edit(doc: _Doc, section: RecordSection, inner: str) -> tuple[int, int, str]:
    heading = f"<h2>{escape(section.heading)}</h2>" if section.heading else ""
    markup = f'<section id="{escape(section.id, quote=True)}" {SECTION_ATTR}="{escape(section.id, quote=True)}">{heading}<ul>{inner}</ul></section>'
    main = doc.main()
    if main is not None:
        footer = next((child for child in main.children if child.tag == "footer"), None)
        position = footer.start if footer is not None else main.close_start
    else:
        footer = next((node for node in doc.nodes if node.tag == "footer"), None)
        body = next((node for node in doc.nodes if node.tag == "body"), None)
        position = footer.start if footer is not None else body.close_start if body is not None else len(doc.html)
    return position, position, markup


def _inject(doc: _Doc, section: RecordSection, record: BriefRecord, freedom: ContentFreedom, repairs: list[str]) -> str:
    page = doc.page
    if not any(_missing(key, value, freedom, page, page) for key, value in record.fields.items()):
        return doc.html
    tagged = doc.tagged(RECORD_ATTR)
    styles = _section_styles(doc, section, record.id)
    siblings = [node for other in section.records if other.id != record.id for node in tagged.get(other.id, [])[:1]]
    if siblings:
        sibling = max(siblings, key=lambda node: node.end)
        markup = _record_markup(record, styles, sibling.tag, _attrs(sibling))
        edit = (sibling.end, sibling.end, markup)
        repairs.append(f"fallback: injected record {record.id}")
        return _apply(doc.html, [edit])
    markup = _record_markup(record, styles, "li", "")
    container = _find_section(doc, section)
    if container is not None:
        repairs.append(f"fallback: injected record {record.id}")
        return _apply(doc.html, [(container.close_start, container.close_start, f"<ul>{markup}</ul>")])
    repairs.append(f"fallback: created section {section.id} for record {record.id}")
    return _apply(doc.html, [_new_section_edit(doc, section, markup)])


def _sync_record(html: str, section: RecordSection, record: BriefRecord, freedom: ContentFreedom, repairs: list[str]) -> str:
    doc = _Doc(html)
    tagged = doc.tagged(RECORD_ATTR).get(record.id, [])
    node = tagged[0] if tagged else None
    located = node
    if node is None:
        node = _locate(doc, record, freedom)
        if node is None:
            return _inject(doc, section, record, freedom, repairs)
    fields = doc.fields_of(node)
    overwrites = {
        key: _overwrite(doc, fields[key], value)
        for key, value in record.fields.items()
        if key in fields and isinstance(value, str) and _is_fact(key, value) and not _is_prose(key, freedom)
    }
    shifted = [(edit[0] - node.start, edit[1] - node.start, edit[2]) for edit in overwrites.values() if edit is not None]
    view = _view(_apply(doc.fragment(node), shifted))
    styles = _section_styles(doc, section, record.id)
    prefix = "tagged" if located is not None else "fallback"
    edits: list[tuple[int, int, str]] = []
    tail: list[str] = []
    notes: list[str] = []
    for key, value in record.fields.items():
        element = fields.get(key)
        is_list = isinstance(value, list)
        if key in overwrites:
            edit = overwrites[key]
            if edit is not None:
                edits.append(edit)
                notes.append(f"{prefix}: overwrote {record.id}.{key}")
                continue
            if not element.children:
                continue
        missing = _missing(key, value, freedom, view, doc.page)
        if not missing:
            continue
        if element is not None and is_list and (element.children or element.tag in ("ul", "ol")):
            edits.append((element.close_start, element.close_start, _list_entries(missing, element)))
        elif element is not None and is_list:
            anchor = element.parent if element.parent is not node and element.parent.tag == "li" else element
            siblings = [_field_markup(key, [item], False, element, node.tag) for item in missing]
            if anchor is not element:
                siblings = [f"<li{_attrs(anchor)}>{sibling}</li>" for sibling in siblings]
            separator = "" if anchor is not element else " "
            edits.append((anchor.end, anchor.end, "".join(f"{separator}{sibling}" for sibling in siblings)))
        else:
            tail.append(_field_markup(key, missing if is_list else [missing[0]], is_list, styles.get(key), node.tag))
        notes.append(f"{prefix}: appended {record.id}.{key}")
    if tail:
        edits.append((node.close_start, node.close_start, "".join(tail)))
    if not edits:
        return html
    if located is None:
        position = node.start + 1 + len(node.tag)
        edits.append((position, position, f' {RECORD_ATTR}="{escape(record.id, quote=True)}"'))
    repairs.extend(notes)
    return _apply(html, edits)


def sync_brief_records(html: str, records: list[RecordSection], freedom: ContentFreedom) -> tuple[str, list[str]]:
    """Make the page carry every brief record, verbatim where it states a fact.

    A record element tagged `data-wra-record` has its tagged fact fields
    overwritten and its missing fields appended; a record the page lost is
    injected beside its tagged siblings or into its section, and a lost section
    is created inside `<main>`. Prose fields are only restored when none of their
    key words is on the page, the same test the fact-loss check applies.
    """
    if not records:
        return html, []
    repairs: list[str] = []
    html = _strip_duplicate_ids(html, {record.id for section in records for record in section.records}, repairs)
    for section in records:
        for record in section.records:
            html = _sync_record(html, section, record, freedom, repairs)
    return html, repairs
