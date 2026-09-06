from dataclasses import dataclass, field
from pathlib import Path
import logging
import re
import shutil

logger = logging.getLogger(__name__)

HTML_EXTENSIONS = {".html", ".htm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mov"}
FONT_EXTENSIONS = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
BRIEF_EXTENSIONS = {".md", ".txt", ".yaml", ".yml"}
IGNORED_DIRECTORIES = {".git", "node_modules", "__pycache__", ".cache", ".idea", ".vscode"}

_ENTRY_PREFERENCE = ("index.html", "index.htm", "input.html")
_URL_ATTRIBUTE_RE = re.compile(r"""(\b(?:src|href|poster|data-src)\s*=\s*)(["'])([^"']*)\2""", re.I)
_SRCSET_RE = re.compile(r"""(\bsrcset\s*=\s*)(["'])([^"']*)\2""", re.I)
_CSS_URL_RE = re.compile(r"""(url\(\s*)(["']?)([^"')]+)\2(\s*\))""", re.I)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.I | re.S)
_STYLESHEET_LINK_RE = re.compile(r"""<link\b[^>]*\brel\s*=\s*["']?stylesheet["']?[^>]*>""", re.I)
_SCRIPT_SRC_RE = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>""", re.I)
_HREF_RE = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.I)


class InputError(ValueError):
    """Raised when the input directory cannot produce a usable page."""


@dataclass(frozen=True)
class InputFile:
    path: Path
    relative: str
    kind: str
    bytes: int


@dataclass(frozen=True)
class InputPage:
    path: Path
    relative: str
    slug: str
    html: str


@dataclass(frozen=True)
class InputBundle:
    root: Path
    entry: InputPage | None
    pages: list[InputPage]
    files: list[InputFile]
    # Set when the folder describes a site to create rather than one to
    # redesign: no HTML, just a few lines saying what the page is for.
    brief: str = ""
    # Media the redesign may place. This is what the models are shown.
    asset_map: dict[str, str] = field(default_factory=dict)
    # Everything the source page needs to render, stylesheets and scripts
    # included. Used to copy files and to rewrite the preserved source page, so
    # that its screenshot looks like the real site rather than unstyled markup.
    render_map: dict[str, str] = field(default_factory=dict)

    def files_of(self, kind: str) -> list[InputFile]:
        return [item for item in self.files if item.kind == kind]

    @property
    def creates(self) -> bool:
        """Whether this input asks for a new site rather than a redesign."""
        return not self.pages


def classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in HTML_EXTENSIONS:
        return "html"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix == ".css":
        return "css"
    if suffix in {".js", ".mjs"}:
        return "js"
    if suffix in FONT_EXTENSIONS:
        return "font"
    if suffix in BRIEF_EXTENSIONS:
        return "brief"
    return "other"


def _is_ignored(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in IGNORED_DIRECTORIES for part in relative.parts[:-1]):
        return True
    name = relative.name
    return name.startswith(".") or name.startswith("_")


def _slug_for(relative: str) -> str:
    stem = re.sub(r"\.html?$", "", relative, flags=re.I)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    return slug or "page"


def scan(directory: str | Path) -> list[InputFile]:
    root = Path(directory)
    if not root.is_dir():
        raise InputError(f"Input directory not found: {root}")
    files: list[InputFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_ignored(path, root):
            continue
        kind = classify(path)
        if kind == "other" and not path.suffix:
            continue
        files.append(InputFile(path=path, relative=path.relative_to(root).as_posix(), kind=kind, bytes=path.stat().st_size))
    return files


def _select_entry(pages: list[InputFile], root: Path, requested: str | None) -> InputFile:
    if requested:
        wanted = (root / requested).resolve()
        for page in pages:
            if page.path.resolve() == wanted:
                return page
        raise InputError(f"Configured input.entry not found in {root}: {requested}")
    by_name = {page.relative.lower(): page for page in pages}
    for candidate in _ENTRY_PREFERENCE:
        if candidate in by_name:
            return by_name[candidate]
    return max(pages, key=lambda page: page.bytes)


RENDER_KINDS = {"image", "video", "font", "css", "js"}
MEDIA_KINDS = {"image", "video", "font"}


def _free_output(candidate: str, taken: set[str]) -> str:
    """Number the stem until the output path is one no other file holds.

    A file at the input root has no directory to be kept apart by, so its
    source path is its basename and cannot disambiguate it from a same-named
    file deeper in the tree.
    """
    if candidate.lower() not in taken:
        return candidate
    stem, _, suffix = candidate.rpartition(".")
    counter = 2
    while f"{stem}-{counter}.{suffix}".lower() in taken:
        counter += 1
    return f"{stem}-{counter}.{suffix}"


def _build_render_map(files: list[InputFile], asset_directory: str) -> dict[str, str]:
    """Map each file to its output path under the run's asset directory.

    Every file is keyed by its source path, which is unique, and by its
    basename as well when no other file claimed that name first. Source markup
    addresses assets by paths that need not exist locally (`/src/img/logo.png`),
    so the basename is often the only join between a reference and a delivered
    file -- but it cannot be the only key: two files named `logo.png` would then
    share one entry, one of them would never be copied, and every reference to
    it would silently resolve to the other file's image.

    Source paths are therefore assigned first and basenames only aliased
    afterwards, so an alias can never take a key a real source path needs: for
    a file at the input root the two are the same string.
    """
    mapping: dict[str, str] = {}
    holder: dict[str, str] = {}
    taken: set[str] = set()
    renderable = [item for item in files if item.kind in RENDER_KINDS]
    for item in renderable:
        name = Path(item.relative).name
        first = holder.setdefault(name.lower(), item.relative)
        if first == item.relative:
            output = f"{asset_directory}/{name}"
        else:
            logger.warning("Duplicate asset basename kept apart from %s: %s (both are named '%s')", first, item.relative, name)
            output = _free_output(f"{asset_directory}/{item.relative}", taken)
        mapping[item.relative.lower()] = output
        taken.add(output.lower())
    for item in renderable:
        key = Path(item.relative).name.lower()
        if key not in mapping:
            mapping[key] = mapping[item.relative.lower()]
    return mapping


def read_brief(files: list[InputFile]) -> str:
    """Join every brief file in the folder, structured ones last.

    A YAML brief states things the prose may only imply, so it is placed where
    it is read last and therefore settles anything the prose left ambiguous.
    """
    briefs = [item for item in files if item.kind == "brief"]
    if not briefs:
        return ""
    briefs.sort(key=lambda item: (Path(item.relative).suffix.lower() in {".yaml", ".yml"}, item.relative))
    parts = []
    for item in briefs:
        text = item.path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parts.append(f"### {item.relative}\n{text}")
    return "\n\n".join(parts)


def discover(directory: str | Path, entry: str | None = None, asset_directory: str = "assets") -> InputBundle:
    root = Path(directory)
    files = scan(root)
    html_files = [item for item in files if item.kind == "html"]
    brief = read_brief(files)
    if not html_files and not brief:
        raise InputError(f"Nothing to work from in {root}. Place an .html file to redesign, or a .md/.txt/.yaml brief to create from.")

    render_map = _build_render_map(files, asset_directory)
    media = [item for item in files if item.kind in MEDIA_KINDS]
    media_keys = {item.relative.lower() for item in media} | {Path(item.relative).name.lower() for item in media}
    asset_map = {key: target for key, target in render_map.items() if key in media_keys}

    if not html_files:
        bundle = InputBundle(root=root, entry=None, pages=[], files=files, brief=brief, asset_map=asset_map, render_map=render_map)
        logger.info("Input discovered: root=%s brief_chars=%d images=%d (creating, no source page)", root, len(brief), len(bundle.files_of("image")))
        return bundle

    entry_file = _select_entry(html_files, root, entry)
    ordered = [entry_file] + [item for item in html_files if item.relative != entry_file.relative]
    pages = [InputPage(path=item.path, relative=item.relative, slug=_slug_for(item.relative), html=item.path.read_text(encoding="utf-8", errors="replace")) for item in ordered]
    bundle = InputBundle(root=root, entry=pages[0], pages=pages, files=files, brief=brief, asset_map=asset_map, render_map=render_map)
    logger.info("Input discovered: root=%s pages=%d entry=%s images=%d css=%d js=%d", root, len(pages), entry_file.relative, len(bundle.files_of("image")), len(bundle.files_of("css")), len(bundle.files_of("js")))
    return bundle


def _mapped(reference: str, asset_map: dict[str, str]) -> str | None:
    candidate = reference.strip()
    if not candidate or candidate.startswith(("http://", "https://", "//", "data:", "#", "mailto:", "tel:")):
        return None
    path = candidate.split("?")[0].split("#")[0].lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    # The source path first: it tells two files of the same name apart. The
    # basename only after that, for markup addressing a path we never received.
    return asset_map.get(path.lower()) or asset_map.get(Path(path).name.lower())


def rewrite_asset_paths(html: str, asset_map: dict[str, str], page_map: dict[str, str] | None = None) -> str:
    """Point every local asset and page reference at its delivered output path."""
    combined = dict(asset_map)
    combined.update(page_map or {})

    def replace_attribute(match: re.Match[str]) -> str:
        target = _mapped(match.group(3), combined)
        return f"{match.group(1)}{match.group(2)}{target}{match.group(2)}" if target else match.group(0)

    def replace_srcset(match: re.Match[str]) -> str:
        parts = []
        for candidate in match.group(3).split(","):
            piece = candidate.strip()
            if not piece:
                continue
            url, _, descriptor = piece.partition(" ")
            target = _mapped(url, combined)
            parts.append(f"{target or url} {descriptor}".strip())
        return f"{match.group(1)}{match.group(2)}{', '.join(parts)}{match.group(2)}"

    def replace_css_url(match: re.Match[str]) -> str:
        target = _mapped(match.group(3), combined)
        return f"{match.group(1)}{match.group(2)}{target}{match.group(2)}{match.group(4)}" if target else match.group(0)

    html = _URL_ATTRIBUTE_RE.sub(replace_attribute, html)
    html = _SRCSET_RE.sub(replace_srcset, html)
    return _CSS_URL_RE.sub(replace_css_url, html)


def page_map_for(bundle: InputBundle) -> dict[str, str]:
    return {Path(page.relative).name.lower(): f"{page.slug}.html" for page in bundle.pages}


def copy_assets(bundle: InputBundle, run_dir: str | Path) -> int:
    """Copy every non-page file into the run directory at its mapped output path."""
    destination_root = Path(run_dir)
    copied = 0
    for item in bundle.files:
        target = bundle.render_map.get(item.relative.lower())
        if target is None:
            continue
        destination = destination_root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.path, destination)
        copied += 1
    logger.info("Assets copied: count=%d destination=%s", copied, destination_root)
    return copied


def _resolve_local(reference: str, page: InputPage, bundle: InputBundle) -> Path | None:
    candidate = reference.strip().split("?")[0].split("#")[0]
    if not candidate or candidate.startswith(("http://", "https://", "//", "data:")):
        return None
    direct = (page.path.parent / candidate.lstrip("/")).resolve()
    if direct.is_file():
        return direct
    name = Path(candidate).name.lower()
    for item in bundle.files:
        if Path(item.relative).name.lower() == name:
            return item.path
    return None


def _read_capped(paths: list[Path], limit: int, label: str) -> str:
    chunks: list[str] = []
    total = 0
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if total + len(text) > limit:
            chunks.append(text[: max(0, limit - total)])
            logger.warning("%s truncated at %d bytes: source=%s", label, limit, path)
            break
        chunks.append(text)
        total += len(text)
    return "\n".join(chunks)


def collect_css(bundle: InputBundle, page: InputPage, limit: int) -> str:
    """Gather the page's own <style> blocks plus every stylesheet it links."""
    inline = "\n".join(_STYLE_BLOCK_RE.findall(page.html))
    linked: list[Path] = []
    for tag in _STYLESHEET_LINK_RE.findall(page.html):
        href = _HREF_RE.search(tag)
        resolved = _resolve_local(href.group(1), page, bundle) if href else None
        if resolved is not None and resolved not in linked:
            linked.append(resolved)
    if not linked:
        linked = [item.path for item in bundle.files_of("css")]
    external = _read_capped(linked, limit, "CSS")
    combined = "\n".join(part for part in (inline, external) if part.strip())
    logger.debug("CSS collected: page=%s inline_chars=%d external_files=%d total_chars=%d", page.relative, len(inline), len(linked), len(combined))
    return combined


def collect_js(bundle: InputBundle, page: InputPage, limit: int) -> str:
    """Gather the page's inline scripts plus every local script it loads."""
    inline = "\n".join(_SCRIPT_BLOCK_RE.findall(page.html))
    linked: list[Path] = []
    for source in _SCRIPT_SRC_RE.findall(page.html):
        resolved = _resolve_local(source, page, bundle)
        if resolved is not None and resolved not in linked:
            linked.append(resolved)
    if not linked:
        linked = [item.path for item in bundle.files_of("js")]
    external = _read_capped(linked, limit, "JavaScript")
    return "\n".join(part for part in (inline, external) if part.strip())
