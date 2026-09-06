from dataclasses import dataclass
from pathlib import Path
import json
import logging
import shutil

from wra.analyzer.assets import build_asset_infos, build_script_digest, build_style_digest
from wra.analyzer.vision import VisionAnalyzer, render_source_page
from wra.core.inputs import InputBundle, InputPage, collect_css, collect_js, copy_assets, page_map_for, rewrite_asset_paths
from wra.core.models import BusinessInfo, Content, FeatureModel, Media, Requirements, SourceAssets, Structure, WRAConfig
from wra.core.output import DATA_DIRECTORY
from wra.creator.creator import feature_model_for
from wra.designer.site_plan import SitePlan, SitePlanPage, identity_plan

logger = logging.getLogger(__name__)

STAGING_DIRECTORY = ".source"


@dataclass(frozen=True)
class SourcePage:
    """One analysed source page, before the output page set is decided."""

    slug: str
    relative: str
    html: str
    features: FeatureModel
    staging_dir: Path

    def summary(self) -> dict:
        """The compact description the site planner reads."""
        content = self.features.content
        return {
            "slug": self.slug,
            "title": self.features.metadata.title or self.slug,
            "description": self.features.metadata.description or "",
            "headings": [heading.text for heading in content.headings[:12] if heading.text],
            "paragraphs": len(content.paragraphs),
            "lists": len(content.lists),
            "tables": len(content.tables),
            "images": len(self.features.media.images),
            "forms": len(self.features.structure.forms),
            "text_chars": sum(len(text) for text in content.text),
        }


@dataclass(frozen=True)
class PreparedPage:
    """One page of the redesigned site, ready for the iteration loop."""

    slug: str
    relative: str
    html: str
    features: FeatureModel
    # The run directory: finished pages land here as `<slug>.html`, beside
    # the shared `assets/` their references resolve against.
    run_root: Path
    # This page's own source material: features, original, screenshot.
    data_dir: Path
    is_entry: bool
    sources: tuple[str, ...] = ()
    content_focus: str = ""

    @property
    def site_path(self) -> Path:
        return self.run_root / f"{self.slug}.html"


def merge_feature_models(models: list[FeatureModel], title: str = "") -> FeatureModel:
    """Combine several source pages into the material for one output page."""
    if len(models) == 1 and not title:
        return models[0]

    primary = models[0]
    merged = primary.model_copy(deep=True)
    if title:
        merged.metadata.title = title

    for other in models[1:]:
        merged.metadata.keywords = list(dict.fromkeys([*merged.metadata.keywords, *other.metadata.keywords]))
        merged.metadata.description = merged.metadata.description or other.metadata.description
        merged.metadata.language = merged.metadata.language or other.metadata.language
        merged.content = Content(
            headings=[*merged.content.headings, *other.content.headings],
            paragraphs=[*merged.content.paragraphs, *other.content.paragraphs],
            lists=[*merged.content.lists, *other.content.lists],
            tables=[*merged.content.tables, *other.content.tables],
            buttons=[*merged.content.buttons, *other.content.buttons],
            links=[*merged.content.links, *other.content.links],
            labels=list(dict.fromkeys([*merged.content.labels, *other.content.labels])),
            text=[*merged.content.text, *other.content.text],
        )
        merged.structure = Structure(
            header=merged.structure.header or other.structure.header,
            navigation=merged.structure.navigation or other.structure.navigation,
            main=merged.structure.main or other.structure.main,
            sections=[*merged.structure.sections, *other.structure.sections],
            articles=merged.structure.articles or other.structure.articles,
            aside=merged.structure.aside or other.structure.aside,
            footer=merged.structure.footer or other.structure.footer,
            forms=[*merged.structure.forms, *other.structure.forms],
        )
        merged.media = Media(
            images=_unique_models([*merged.media.images, *other.media.images]),
            videos=_unique_models([*merged.media.videos, *other.media.videos]),
            icons=list(dict.fromkeys([*merged.media.icons, *other.media.icons])),
            urls=list(dict.fromkeys([*merged.media.urls, *other.media.urls])),
        )
        merged.business = _merge_business(merged.business, other.business)
        merged.navigation = merged.navigation or other.navigation
        merged.requirements = Requirements(
            must_preserve=list(dict.fromkeys([*merged.requirements.must_preserve, *other.requirements.must_preserve])),
            optional=list(dict.fromkeys([*merged.requirements.optional, *other.requirements.optional])),
        )
    return merged


def retarget_links(features: FeatureModel, link_map: dict[str, str]) -> None:
    """Point links at the pages that survived the restructure, in place.

    A merged-away page keeps being linked to otherwise: its href sits in the
    Feature Model, and the generator faithfully writes a link to a file the
    redesigned site no longer has.
    """
    if not link_map:
        return
    for link in features.content.links:
        link.href = _retargeted(link.href, link_map)
    for button in features.content.buttons:
        button.href = _retargeted(button.href, link_map)


def _retargeted(href: str | None, link_map: dict[str, str]) -> str | None:
    if not href:
        return href
    target, _, fragment = href.partition("#")
    replacement = link_map.get(target.strip().lower())
    if replacement is None:
        return href
    return f"{replacement}#{fragment}" if fragment else replacement


def _unique_models(items: list) -> list:
    seen: set[str] = set()
    unique = []
    for item in items:
        key = item.model_dump_json()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _merge_business(base: BusinessInfo | None, other: BusinessInfo | None) -> BusinessInfo | None:
    if base is None or other is None:
        return base or other
    data = base.model_dump()
    for key, value in other.model_dump().items():
        if isinstance(value, list):
            data[key] = list(dict.fromkeys([*data[key], *value]))
        elif data.get(key) is None:
            data[key] = value
    return BusinessInfo.model_validate(data)


class InputPreparer:
    """Turn an input folder into the pages the iteration loop will redesign.

    Everything derived here — the style and script digests, the asset
    inventory, the vision insights — is reused unchanged by every iteration,
    so it is computed once per page rather than once per iteration.

    Source pages are analysed first and staged, because at `madmax` freedom the
    site planner may decide the redesigned site has a different page set than
    the source did.
    """

    def __init__(self, config: WRAConfig, analyzer, vision_analyzer: VisionAnalyzer | None = None, site_planner=None, creator=None) -> None:
        self._config = config
        self._analyzer = analyzer
        self._vision = vision_analyzer
        self._site_planner = site_planner
        self._creator = creator

    def prepare(self, bundle: InputBundle, run_dir: str | Path) -> list[PreparedPage]:
        root = Path(run_dir)
        root.mkdir(parents=True, exist_ok=True)
        copy_assets(bundle, root)

        mode = self._config.input.mode
        pages = [bundle.entry] if mode == "entry" else bundle.pages
        page_map = page_map_for(bundle) if mode != "entry" else {}
        asset_infos = build_asset_infos(bundle)
        source_paths = {info.path: item.path for info, item in self._pair_sources(bundle, asset_infos)}
        self._apply_asset_vision(asset_infos, source_paths)

        if bundle.creates:
            return self._create_pages(bundle, asset_infos, root)

        staging = root / STAGING_DIRECTORY
        sources = [self._analyze_source(bundle, page, page_map, asset_infos, staging, root) for page in pages]
        plan = self._plan_site(sources)
        prepared = self._materialize(plan, sources, root)
        shutil.rmtree(staging, ignore_errors=True)
        logger.info("Input prepared: mode=%s sources=%d pages=%d", mode, len(sources), len(prepared))
        return prepared

    def _create_pages(self, bundle: InputBundle, asset_infos, root: Path) -> list[PreparedPage]:
        """Build the site the brief describes, with no source page to work from."""
        if self._creator is None:
            raise ValueError("The input folder has no HTML to redesign and no creator is configured")
        draft = self._creator.create(bundle.brief, asset_infos)
        (root / DATA_DIRECTORY).mkdir(parents=True, exist_ok=True)
        (root / DATA_DIRECTORY / "brief.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")

        prepared: list[PreparedPage] = []
        for index, page in enumerate(draft.pages):
            data_dir = root / DATA_DIRECTORY / page.slug
            data_dir.mkdir(parents=True, exist_ok=True)
            features = feature_model_for(draft, page, asset_infos)
            # No source page means no source styles, scripts, or impression;
            # only the media the client supplied carries over.
            features.source_assets = SourceAssets(assets=asset_infos)
            (data_dir / "created.json").write_text(page.model_dump_json(indent=2), encoding="utf-8")
            logger.info("Created page ready: slug=%s entry=%s headings=%d images=%d", page.slug, index == 0, len(page.headings), len(page.images))
            prepared.append(PreparedPage(
                slug=page.slug,
                relative=f"{page.slug} (created)",
                html="",
                features=features,
                run_root=root,
                data_dir=data_dir,
                is_entry=index == 0,
                content_focus=page.purpose,
            ))
        logger.info("Site created from brief: pages=%d", len(prepared))
        return prepared

    def _analyze_source(self, bundle, page: InputPage, page_map, asset_infos, staging: Path, run_root: Path) -> SourcePage:
        staging_dir = staging / page.slug
        staging_dir.mkdir(parents=True, exist_ok=True)
        html = rewrite_asset_paths(page.html, bundle.render_map, page_map)
        (staging_dir / "original.html").write_text(html, encoding="utf-8")
        logger.info("Analyzing source page: relative=%s slug=%s", page.relative, page.slug)

        features = self._analyzer.analyze(html)
        features.source_assets = SourceAssets(
            styles=build_style_digest(collect_css(bundle, page, self._config.input.max_css_bytes)),
            scripts=build_script_digest(collect_js(bundle, page, self._config.input.max_js_bytes)),
            assets=asset_infos,
            impression=self._page_impression(html, run_root, staging_dir, page.slug),
        )
        logger.info("Source assets attached: page=%s json_chars=%d", page.slug, len(features.source_assets.model_dump_json()))
        return SourcePage(slug=page.slug, relative=page.relative, html=html, features=features, staging_dir=staging_dir)

    def _plan_site(self, sources: list[SourcePage]) -> SitePlan:
        """Let the planner reshape the page set, but only where that is allowed."""
        identity = identity_plan([(source.slug, source.features.metadata.title or source.slug) for source in sources])
        if self._site_planner is None:
            return identity
        if self._config.content.freedom != "madmax":
            logger.info("Site restructuring skipped: content freedom is '%s', not 'madmax'", self._config.content.freedom)
            return identity
        if self._config.input.mode == "entry":
            logger.info("Site restructuring skipped: input.mode 'entry' redesigns the entry page alone")
            return identity
        try:
            return self._site_planner.plan([source.summary() for source in sources])
        except Exception as exc:
            # A restructure is an improvement, not a requirement: falling back to
            # the source's own page set still produces the redesign.
            logger.warning("Site restructuring failed, keeping the source page structure: %s", exc)
            return identity

    def _materialize(self, plan: SitePlan, sources: list[SourcePage], root: Path) -> list[PreparedPage]:
        by_slug = {source.slug: source for source in sources}
        link_map = self._link_map(plan)
        prepared: list[PreparedPage] = []
        for index, planned in enumerate(plan.pages):
            page_sources = [by_slug[slug] for slug in planned.sources]
            data_dir = root / DATA_DIRECTORY / planned.slug
            data_dir.mkdir(parents=True, exist_ok=True)
            self._place_source_artifacts(planned, page_sources, data_dir)
            features = merge_feature_models([source.features for source in page_sources], planned.title)
            retarget_links(features, link_map)
            logger.info("Output page ready: slug=%s entry=%s sources=%s", planned.slug, index == 0, planned.sources)
            prepared.append(PreparedPage(
                slug=planned.slug,
                relative=page_sources[0].relative,
                html=page_sources[0].html,
                features=features,
                run_root=root,
                data_dir=data_dir,
                is_entry=index == 0,
                sources=tuple(planned.sources),
                content_focus=planned.content_focus,
            ))
        return prepared

    @staticmethod
    def _link_map(plan: SitePlan) -> dict[str, str]:
        """Where each source page's links should now point.

        A source that fed exactly one output page redirects to it; a source that
        was split across several redirects to the first, which the planner lists
        as that material's main home.
        """
        mapping: dict[str, str] = {}
        for page in plan.pages:
            for source in page.sources:
                mapping.setdefault(f"{source}.html".lower(), f"{page.slug}.html")
        return {source: target for source, target in mapping.items() if source != target}

    @staticmethod
    def _place_source_artifacts(planned: SitePlanPage, page_sources: list[SourcePage], data_dir: Path) -> None:
        """Carry each output page's source material into its own directory."""
        primary = page_sources[0]
        shutil.copyfile(primary.staging_dir / "original.html", data_dir / "original.html")
        screenshot = primary.staging_dir / "source.png"
        if screenshot.is_file():
            shutil.copyfile(screenshot, data_dir / "source.png")
        for extra in page_sources[1:]:
            shutil.copyfile(extra.staging_dir / "original.html", data_dir / f"original_{extra.slug}.html")
        if len(page_sources) > 1 or planned.reason not in {"", "unchanged"}:
            (data_dir / "sources.json").write_text(
                json.dumps({"sources": list(planned.sources), "content_focus": planned.content_focus, "reason": planned.reason}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @staticmethod
    def _pair_sources(bundle: InputBundle, asset_infos):
        by_output = {}
        for item in bundle.files:
            output = bundle.asset_map.get(Path(item.relative).name.lower())
            if output is not None:
                by_output.setdefault(output, item)
        return [(info, by_output[info.path]) for info in asset_infos if info.path in by_output]

    def _apply_asset_vision(self, asset_infos, source_paths) -> None:
        if self._vision is None:
            logger.info("Vision analysis disabled or unconfigured: media described from file headers only")
            return
        self._vision.analyze_assets(asset_infos, source_paths)

    def _page_impression(self, html: str, run_root: Path, output_dir: Path, slug: str):
        if self._vision is None or not self._config.input.vision.screenshot:
            return None
        # Rendered beside the copied assets so that `assets/...` resolves, but
        # captured into this page's own directory.
        screenshot = render_source_page(html, run_root, output_dir / "source.png", self._config.browser, reveal_animations=self._config.input.vision.reveal_animations)
        if screenshot is None:
            return None
        logger.info("Source screenshot stored: page=%s path=%s", slug, screenshot)
        return self._vision.analyze_page(screenshot)
