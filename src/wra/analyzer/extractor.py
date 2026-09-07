import re
from collections.abc import Iterable

from wra.analyzer.html_parser import HTMLNode
from wra.core.models import (
    BusinessInfo,
    Button,
    Content,
    Form,
    Heading,
    Image,
    Link,
    Media,
    Metadata,
    Requirements,
    Section,
    Structure,
    Video,
)


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SEMANTIC_TAGS = {"header", "nav", "main", "section", "article", "aside", "footer", "form"}
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?<![\d)])(?:\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]\d{3,4}[\s.-]\d{4}|\(?0\d{1,2}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}|1[5-8]\d{2}[\s.-]?\d{4})(?!\d)")


def iter_nodes(root: HTMLNode) -> Iterable[HTMLNode]:
    for child in root.children:
        yield child
        yield from iter_nodes(child)


def find_nodes(root: HTMLNode, tag: str) -> list[HTMLNode]:
    return [node for node in iter_nodes(root) if node.tag == tag]


def extract_metadata(root: HTMLNode) -> Metadata:
    title_nodes = find_nodes(root, "title")
    description = None
    keywords: list[str] = []
    canonical = None
    open_graph: dict[str, str] = {}

    for node in find_nodes(root, "meta"):
        name = node.attrs.get("name", "").lower()
        property_name = node.attrs.get("property", "").lower()
        content = node.attrs.get("content", "")
        if name == "description":
            description = content
        elif name == "keywords":
            keywords = [item.strip() for item in content.split(",") if item.strip()]
        elif property_name.startswith("og:"):
            open_graph[property_name] = content

    canonical_nodes = [node for node in find_nodes(root, "link") if node.attrs.get("rel", "").lower() == "canonical"]
    if canonical_nodes:
        canonical = canonical_nodes[0].attrs.get("href") or None

    html_nodes = find_nodes(root, "html")
    language = html_nodes[0].attrs.get("lang") or None if html_nodes else None

    return Metadata(
        title=title_nodes[0].text_content() if title_nodes else None,
        description=description,
        keywords=keywords,
        language=language,
        canonical=canonical,
        open_graph=open_graph,
    )


def extract_content(root: HTMLNode) -> Content:
    headings = [Heading(level=int(node.tag[1]), text=node.text_content()) for node in iter_nodes(root) if len(node.tag) == 2 and node.tag[0] == "h" and node.tag[1].isdigit()]
    paragraphs = [node.text_content() for node in find_nodes(root, "p") if node.text_content()]
    links = [Link(text=node.text_content(), href=node.attrs.get("href") or None) for node in find_nodes(root, "a")]
    buttons: list[Button] = []
    for node in find_nodes(root, "button"):
        buttons.append(Button(text=node.text_content(), href=node.attrs.get("formaction") or None))
    for node in find_nodes(root, "a"):
        if "button" in node.attrs.get("class", "").lower():
            buttons.append(Button(text=node.text_content(), href=node.attrs.get("href") or None))
    labels = [node.text_content() for node in find_nodes(root, "label") if node.text_content()]

    lists: list[list[str]] = []
    for node in find_nodes(root, "ul") + find_nodes(root, "ol"):
        items = [child.text_content() for child in node.children if child.tag == "li" and child.text_content()]
        if items:
            lists.append(items)

    tables: list[list[list[str]]] = []
    for table in find_nodes(root, "table"):
        rows: list[list[str]] = []
        for row in find_nodes(table, "tr"):
            cells = [cell.text_content() for cell in row.children if cell.tag in {"th", "td"} and cell.text_content()]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)

    return Content(
        headings=headings,
        paragraphs=paragraphs,
        lists=lists,
        tables=tables,
        buttons=buttons,
        links=links,
        labels=labels,
        text=[node.text_content() for node in find_nodes(root, "body") if node.text_content()],
    )


def extract_structure(root: HTMLNode) -> Structure:
    forms: list[Form] = []
    for node in find_nodes(root, "form"):
        fields = []
        for field in iter_nodes(node):
            if field.tag in {"input", "select", "textarea"}:
                name = field.attrs.get("name") or field.attrs.get("id")
                if name:
                    fields.append(name)
        forms.append(Form(action=node.attrs.get("action") or None, method=node.attrs.get("method") or None, fields=fields))

    sections = [Section(tag=node.tag, text=node.text_content()) for node in iter_nodes(root) if node.tag == "section"]
    return Structure(
        header=bool(find_nodes(root, "header")),
        navigation=bool(find_nodes(root, "nav")),
        main=bool(find_nodes(root, "main")),
        sections=sections,
        articles=bool(find_nodes(root, "article")),
        aside=bool(find_nodes(root, "aside")),
        footer=bool(find_nodes(root, "footer")),
        forms=forms,
    )


def extract_media(root: HTMLNode) -> Media:
    images = [Image(src=node.attrs.get("src") or None, alt=node.attrs.get("alt") or None) for node in find_nodes(root, "img")]
    videos = [Video(src=node.attrs.get("src") or None, poster=node.attrs.get("poster") or None) for node in find_nodes(root, "video")]
    urls = [image.src for image in images if image.src] + [video.src for video in videos if video.src]
    icons = [node.attrs.get("class", "") for node in iter_nodes(root) if "icon" in node.attrs.get("class", "").lower()]
    return Media(images=images, videos=videos, icons=icons, urls=urls)


def extract_business_info(root: HTMLNode, content: Content) -> BusinessInfo:
    text = " ".join(content.text)
    emails = sorted(set(EMAIL_RE.findall(text)))
    phones = sorted(set(PHONE_RE.findall(text)))
    addresses = [node.text_content() for node in find_nodes(root, "address") if node.text_content()]
    cta = [button.text for button in content.buttons if button.text]
    return BusinessInfo(
        contact=emails + phones,
        address=addresses[0] if addresses else None,
        email=emails,
        phone=phones,
        cta=cta,
    )


def extract_requirements(content: Content, media: Media) -> Requirements:
    must_preserve = [heading.text for heading in content.headings if heading.text]
    must_preserve.extend(button.text for button in content.buttons if button.text)
    must_preserve.extend(image.alt for image in media.images if image.alt)
    return Requirements(must_preserve=must_preserve)
