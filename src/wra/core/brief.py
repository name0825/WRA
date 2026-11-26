from dataclasses import dataclass, field
from pathlib import Path
import logging
import re

import yaml

from wra.core.models import BriefRecord, RecordSection
from wra.validator.brief_facts import _FLOW_LIST_RE, _HEADING_RE, _KEY_RE, _LABEL_RE, _LIST_ITEM_RE, _is_directive, _is_group_label

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class BriefDirectives:
    """Design decisions a structured brief fixes, as opposed to prose it leaves open."""

    palette: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, str] = field(default_factory=dict)
    logo: str | None = None
    layout: list[str] = field(default_factory=list)
    must: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    nav: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.palette or self.fonts or self.logo or self.layout or self.must or self.avoid or self.nav)


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip()]


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _parse(data) -> BriefDirectives | None:
    if not isinstance(data, dict):
        return None
    design = _mapping(data.get("design"))
    palette = {str(key): value.strip() for key, value in _mapping(design.get("palette")).items() if isinstance(value, str) and _HEX_RE.match(value.strip())}
    fonts = {str(key): value.strip() for key, value in _mapping(design.get("typography")).items() if isinstance(value, str) and value.strip()}
    logo = design.get("logo") or _mapping(data.get("site")).get("logo")
    directives = BriefDirectives(
        palette=palette,
        fonts=fonts,
        logo=logo.strip() if isinstance(logo, str) and logo.strip() else None,
        layout=_strings(design.get("layout")),
        must=_strings(design.get("must")),
        avoid=_strings(design.get("avoid")),
        nav=_strings(data.get("nav")),
    )
    return None if directives.is_empty() else directives


class _TextLoader(yaml.SafeLoader):
    """Reads `2022.10` as the string it was written as, not as the float 2022.1."""


_TextLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:float"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _yaml_briefs(files, loader=yaml.SafeLoader):
    for item in files:
        if item.kind != "brief" or Path(item.relative).suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            yield yaml.load(item.path.read_text(encoding="utf-8", errors="replace"), Loader=loader)
        except Exception:
            logger.info("Brief is not valid YAML, using it as text: %s", item.relative)


def parse_brief_directives(files) -> BriefDirectives | None:
    """Read the design directives out of the first YAML brief that has any.

    Anything that is not a well-formed YAML mapping, markdown and plain text
    included, yields None and leaves the raw brief text to do the work.
    """
    for data in _yaml_briefs(files):
        directives = _parse(data)
        if directives is not None:
            return directives
    return None


_IMAGE_KEYS = frozenset({"image", "img", "src", "thumbnail", "icon", "logo", "photo", "picture", "cover"})


def _scalar(value) -> str | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip() or None
    return None


def _field_value(value) -> str | list[str] | None:
    if isinstance(value, list):
        return [text for text in (_scalar(item) for item in value) if text] or None
    return _scalar(value)


def _record_fields(raw) -> dict[str, str | list[str]]:
    if not isinstance(raw, dict):
        text = _scalar(raw)
        return {"text": text} if text else {}
    fields = {}
    for key, value in raw.items():
        if str(key).lower() in _IMAGE_KEYS:
            continue
        converted = _field_value(value)
        if converted is not None:
            fields[str(key)] = converted
    return fields


def _listed(value) -> list:
    return value if isinstance(value, list) else []


def _section_records(index: int, section: dict, used: set[str]) -> RecordSection | None:
    section_id = _scalar(section.get("id")) or f"section{index}"
    if section_id in used:
        section_id = f"{section_id}{index}"
    used.add(section_id)
    records = []
    body = _record_fields({"body": section.get("body")})
    if body:
        records.append(BriefRecord(id=f"{section_id}.body", fields=body))
    for prefix, key in (("", "items"), ("group", "groups"), ("link", "links"), ("cta", "cta")):
        for number, raw in enumerate(_listed(section.get(key))):
            fields = _record_fields(raw)
            if fields:
                records.append(BriefRecord(id=f"{section_id}.{prefix}{number}", fields=fields))
    heading = _scalar(section.get("heading")) or ""
    return RecordSection(id=section_id, heading=heading, records=records) if records else None


def parse_brief_records(files) -> list[RecordSection]:
    """The dated entries, links and tag lists a brief states, section by section.

    A YAML brief with a `sections` list gives them directly. Any other brief
    file is read as markdown: each `##` heading is a section and each bullet
    under it a record, so the intro under the title and design directives stay
    out of the page's content.
    """
    for data in _yaml_briefs(files, _TextLoader):
        sections = _listed(data.get("sections")) if isinstance(data, dict) else []
        used: set[str] = set()
        parsed = [_section_records(index, section, used) for index, section in enumerate(sections) if isinstance(section, dict)]
        records = [section for section in parsed if section is not None]
        if records:
            return records
    for item in files:
        if item.kind != "brief":
            continue
        records = _markdown_records(item.path.read_text(encoding="utf-8", errors="replace"))
        if records:
            return records
    return []


_SENTENCE_END_RE = re.compile(r"[.!?。]\s*$|[.!?]\s")
_MAX_LIST_PART = 24


def _split_commas(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in text:
        if char in "([{（":
            depth += 1
        elif char in ")]}）":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return parts


def _bullet_fields(item: str) -> dict[str, str | list[str]] | None:
    labelled = _LABEL_RE.match(item)
    if labelled:
        key = item.split(":", 1)[0].strip()
        return {"text": item} if re.search(r"\d", key) else {key: labelled.group(1).strip()}
    parts = _split_commas(item)
    if len(parts) > 1 and all(part and len(part) <= _MAX_LIST_PART and not _SENTENCE_END_RE.search(part) for part in parts):
        return {"items": parts}
    return {"text": item}


def _markdown_records(text: str) -> list[RecordSection]:
    lines = text.splitlines()
    if not any(len(match.group(1)) >= 2 for match in _HEADING_RE.finditer(text)):
        return []
    sections: list[RecordSection] = []
    current: RecordSection | None = None
    context, level = "", 0

    def add(fields: dict[str, str | list[str]]) -> None:
        current.records.append(BriefRecord(id=f"{current.id}.{len(current.records)}", fields=fields))

    for index, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading:
            level, context = len(heading.group(1)), heading.group(2).strip()
            current = None
            if level >= 2 and not _is_directive(context):
                current = RecordSection(id=f"section{len(sections) + 1}", heading=context)
                sections.append(current)
            continue
        if current is None or not line.strip():
            continue
        flow = _FLOW_LIST_RE.search(line)
        if flow:
            key = line.split(":", 1)[0].lstrip("-* ").strip()
            members = [member.strip() for member in flow.group(1).split(",") if member.strip()]
            if members and not _is_directive(key.lower()):
                add({key: members})
            continue
        match = _LIST_ITEM_RE.match(line)
        if match:
            if _is_directive(context) or _is_group_label(lines, index):
                continue
            fields = _bullet_fields(match.group(1))
            if fields:
                add(fields)
            continue
        key = _KEY_RE.match(line)
        if key:
            context = key.group(1)
        elif not _is_directive(context):
            add({"body": line.strip()})
    return [section for section in sections if section.records]
