import json
import re
from pathlib import Path

import pytest

from wra.core.inputs import discover
from wra.core.models import Button, FeatureModel, Heading, Link, PageImpression, ScriptDigest, SourceAssets, StyleDigest, WRAConfig
from wra.core.preparation import InputPreparer, merge_feature_models, retarget_links
from wra.designer.site_plan import SitePlanError, SitePlanner, identity_plan
from support import sample_config_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUMMARIES = [
    {"slug": "index", "title": "Home"},
    {"slug": "about", "title": "About"},
    {"slug": "history", "title": "History"},
]


class ReplyClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.formats: list = []

    def generate(self, prompt, model, options=None, images=None, *, think=None, format=None, timeout=None):
        self.calls += 1
        self.formats.append(format)
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _plan_json(pages) -> str:
    return json.dumps({"pages": pages})


MERGED = _plan_json([
    {"slug": "index", "title": "Home", "entry": True, "sources": ["index"], "content_focus": "the pitch"},
    {"slug": "company", "title": "Company", "entry": False, "sources": ["about", "history"], "content_focus": "who we are", "reason": "both are thin"},
])


def _planner(client, **kwargs) -> SitePlanner:
    return SitePlanner(llm_client=client, model="test", prompt_path=PROJECT_ROOT / "prompts" / "designer" / "site_plan.md", **kwargs)


def test_merges_two_pages_into_one() -> None:
    client = ReplyClient(MERGED)
    plan = _planner(client).plan(SUMMARIES)
    # A schema, not the bare "json" grammar.
    assert isinstance(client.formats[0], dict)
    assert [page.slug for page in plan.pages] == ["index", "company"]
    assert plan.pages[1].sources == ["about", "history"]
    assert plan.restructures


def test_splits_one_page_into_several() -> None:
    response = _plan_json([
        {"slug": "index", "title": "Home", "entry": True, "sources": ["index"], "content_focus": "the pitch"},
        {"slug": "products", "title": "Products", "entry": False, "sources": ["index"], "content_focus": "the catalogue"},
        {"slug": "about", "title": "About", "entry": False, "sources": ["about", "history"]},
    ])
    plan = _planner(ReplyClient(response)).plan(SUMMARIES)
    assert [page.slug for page in plan.pages] == ["index", "products", "about"]
    assert plan.pages[1].content_focus == "the catalogue"


def test_a_plan_that_drops_a_source_page_is_rejected() -> None:
    """Restructuring reorganises content; it never discards a page."""
    dropped = _plan_json([{"slug": "index", "title": "Home", "entry": True, "sources": ["index"]}])
    with pytest.raises(SitePlanError, match="missing from the site plan"):
        _planner(ReplyClient(dropped), max_retries=0).plan(SUMMARIES)


def test_a_plan_inventing_a_source_page_is_rejected() -> None:
    invented = _plan_json([
        {"slug": "index", "title": "Home", "entry": True, "sources": ["index", "about", "history"]},
        {"slug": "blog", "title": "Blog", "entry": False, "sources": ["news"]},
    ])
    with pytest.raises(SitePlanError, match="unknown source page"):
        _planner(ReplyClient(invented), max_retries=0).plan(SUMMARIES)


def test_a_plan_without_a_leading_entry_page_is_rejected() -> None:
    misordered = _plan_json([
        {"slug": "about", "title": "About", "entry": False, "sources": ["about", "history"]},
        {"slug": "index", "title": "Home", "entry": True, "sources": ["index"]},
    ])
    with pytest.raises(SitePlanError, match="entry"):
        _planner(ReplyClient(misordered), max_retries=0).plan(SUMMARIES)


def test_retry_recovers_from_a_bad_first_response() -> None:
    client = ReplyClient("not json", MERGED)
    plan = _planner(client, max_retries=2).plan(SUMMARIES)
    assert [page.slug for page in plan.pages] == ["index", "company"]
    assert client.calls == 2


def test_slugs_are_normalised_into_file_names() -> None:
    response = _plan_json([
        {"slug": "Index", "title": "Home", "entry": True, "sources": ["index"]},
        {"slug": "Our Company!", "title": "Company", "entry": False, "sources": ["about", "history"]},
    ])
    plan = _planner(ReplyClient(response)).plan(SUMMARIES)
    assert [page.slug for page in plan.pages] == ["index", "our-company"]


def test_identity_plan_keeps_the_source_structure() -> None:
    plan = identity_plan([("index", "Home"), ("about", "About")])
    assert not plan.restructures
    assert plan.pages[0].entry and not plan.pages[1].entry


def test_merge_feature_models_combines_content_and_keeps_the_new_title() -> None:
    first, second = FeatureModel(), FeatureModel()
    first.metadata.title = "About"
    first.content.headings = [Heading(level=1, text="About us")]
    first.content.paragraphs = ["We began in 1998."]
    second.content.headings = [Heading(level=1, text="History")]
    second.content.paragraphs = ["We moved in 2004."]
    second.metadata.keywords = ["history"]

    merged = merge_feature_models([first, second], "Company")
    assert merged.metadata.title == "Company"
    assert [heading.text for heading in merged.content.headings] == ["About us", "History"]
    assert merged.content.paragraphs == ["We began in 1998.", "We moved in 2004."]
    assert merged.metadata.keywords == ["history"]
    # The inputs are untouched: the same source may feed another page too.
    assert first.metadata.title == "About"


def test_merge_feature_models_combines_source_assets() -> None:
    """A merged page must keep every source's design context, not just the first's."""
    first, second = FeatureModel(), FeatureModel()
    first.source_assets = SourceAssets(
        styles=StyleDigest(palette=["#111", "#222"], fonts=["Inter"], layout_signals={"flex": 3}),
        scripts=ScriptDigest(libraries=["jquery"]),
        impression=PageImpression(mood=["calm"], density="dense", layout="grid", strengths=["clear nav"]),
    )
    second.source_assets = SourceAssets(
        styles=StyleDigest(palette=["#222", "#333"], fonts=["Lora"], layout_signals={"flex": 2, "grid": 5}),
        scripts=ScriptDigest(libraries=["swiper"]),
        impression=PageImpression(mood=["bold"], density="airy", layout="stack", weaknesses=["thin copy"]),
    )

    assets = merge_feature_models([first, second], "Company").source_assets
    assert assets.styles.palette == ["#111", "#222", "#333"]
    assert assets.styles.fonts == ["Inter", "Lora"]
    assert assets.styles.layout_signals == {"flex": 5, "grid": 5}
    assert assets.scripts.libraries == ["jquery", "swiper"]
    assert assets.impression.mood == ["calm", "bold"]
    assert assets.impression.strengths == ["clear nav"] and assets.impression.weaknesses == ["thin copy"]
    # No single rendered page survives the merge, so the primary's prose stands.
    assert assets.impression.density == "dense" and assets.impression.layout == "grid"


def test_merged_source_assets_stay_within_the_single_page_caps() -> None:
    """The digest rides in every prompt: merging sources must not grow it without bound."""
    models = []
    # Each source already fills the 14-colour cap, as a busy real page does.
    for page in range(6):
        features = FeatureModel()
        features.source_assets = SourceAssets(styles=StyleDigest(palette=[f"#{page}{index:05x}" for index in range(14)]))
        models.append(features)

    palette = merge_feature_models(models, "Company").source_assets.styles.palette
    assert len(palette) == 14
    # A saturated first source must not spend the whole cap: every source is represented.
    assert {colour[1] for colour in palette} == {"0", "1", "2", "3", "4", "5"}


def test_two_pages_sharing_one_source_do_not_share_state() -> None:
    """A source used by two output pages must not let one page's retargeting leak into the other."""
    source = FeatureModel()
    source.content.links = [Link(text="About", href="about.html")]

    page_a = merge_feature_models([source])
    page_b = merge_feature_models([source])
    retarget_links(page_a, {"about.html": "team.html"})
    retarget_links(page_b, {"about.html": "history.html"})

    assert page_a.content.links[0].href == "team.html"
    assert page_b.content.links[0].href == "history.html"
    assert source.content.links[0].href == "about.html"


def test_root_relative_and_dot_relative_links_are_retargeted() -> None:
    features = FeatureModel()
    features.content.links = [
        Link(text="About", href="/about.html"),
        Link(text="About2", href="./about.html"),
    ]

    retarget_links(features, {"about.html": "company.html"})

    assert [link.href for link in features.content.links] == ["company.html", "company.html"]


class StubAnalyzer:
    """Deterministic extraction: keeps the page's links, which restructuring must retarget."""

    def analyze(self, html: str) -> FeatureModel:
        features = FeatureModel()
        features.metadata.title = "Stub"
        features.content.links = [Link(text=href, href=href) for href in re.findall(r'href="([^"]+)"', html)]
        return features


class StubPlanner:
    def __init__(self, plan) -> None:
        self.plan_result = plan
        self.called = False

    def plan(self, summaries):
        self.called = True
        return self.plan_result


def _config(freedom: str, mode: str) -> WRAConfig:
    data = sample_config_data()
    data["content"] = {"freedom": freedom}
    data["input"] = {**data["input"], "mode": mode}
    data["input"]["vision"] = {**data["input"]["vision"], "enabled": False}
    return WRAConfig.model_validate(data)


NAV = '<nav><a href="index.html">Home</a><a href="about.html">About</a><a href="history.html">History</a></nav>'


def _site(root: Path) -> Path:
    root.mkdir(parents=True)
    for name, body in (("index.html", "Home"), ("about.html", "About"), ("history.html", "History")):
        (root / name).write_text(f"<html><head><title>{body}</title></head><body>{NAV}<h1>{body}</h1></body></html>", encoding="utf-8")
    return root


def _prepare(tmp_path: Path, freedom: str, mode: str, planner):
    bundle = discover(_site(tmp_path / "input"))
    run_dir = tmp_path / "run"
    pages = InputPreparer(config=_config(freedom, mode), analyzer=StubAnalyzer(), site_planner=planner).prepare(bundle, run_dir)
    return pages, run_dir


def test_madmax_applies_the_plan_to_the_output_pages(tmp_path: Path) -> None:
    plan = SitePlanner._parse(MERGED, {"index", "about", "history"})
    planner = StubPlanner(plan)
    pages, run_dir = _prepare(tmp_path, "madmax", "site", planner)

    assert planner.called
    assert [page.slug for page in pages] == ["index", "company"]
    assert pages[1].sources == ("about", "history")
    assert pages[1].content_focus == "who we are"
    assert pages[1].data_dir == run_dir / "data" / "company"
    assert pages[1].site_path == run_dir / "company.html"
    # Both merged sources are preserved beside the page they now feed.
    assert (run_dir / "data" / "company" / "original.html").is_file()
    assert (run_dir / "data" / "company" / "original_history.html").is_file()
    assert json.loads((run_dir / "data" / "company" / "sources.json").read_text(encoding="utf-8"))["sources"] == ["about", "history"]


def test_links_to_a_merged_away_page_are_retargeted() -> None:
    """Nothing may link to a page the restructure removed."""
    features = FeatureModel()
    features.content.links = [
        Link(text="About", href="about.html"),
        Link(text="History", href="history.html#awards"),
        Link(text="Home", href="index.html"),
        Link(text="External", href="https://example.com/about.html"),
    ]
    features.content.buttons = [Button(text="Learn more", href="about.html")]

    retarget_links(features, {"about.html": "company.html", "history.html": "company.html"})

    assert [link.href for link in features.content.links] == [
        "company.html", "company.html#awards", "index.html", "https://example.com/about.html",
    ]
    assert features.content.buttons[0].href == "company.html"


def test_an_unchanged_structure_retargets_nothing() -> None:
    plan = identity_plan([("index", "Home"), ("about", "About")])
    assert InputPreparer._link_map(plan) == {}


def test_a_split_page_sends_links_to_its_first_home() -> None:
    plan = SitePlanner._parse(_plan_json([
        {"slug": "index", "title": "Home", "entry": True, "sources": ["index"]},
        {"slug": "products", "title": "Products", "entry": False, "sources": ["index", "about", "history"]},
    ]), {"index", "about", "history"})
    assert InputPreparer._link_map(plan) == {"about.html": "products.html", "history.html": "products.html"}


def test_merged_pages_stop_linking_to_the_page_they_absorbed(tmp_path: Path) -> None:
    plan = SitePlanner._parse(MERGED, {"index", "about", "history"})
    pages, _ = _prepare(tmp_path, "madmax", "site", StubPlanner(plan))
    hrefs = {link.href for page in pages for link in page.features.content.links}
    assert hrefs, "the fixture must carry links for this to mean anything"
    assert "about.html" not in hrefs
    assert "history.html" not in hrefs
    assert {"index.html", "company.html"} <= hrefs


def test_lower_freedom_never_restructures(tmp_path: Path) -> None:
    planner = StubPlanner(SitePlanner._parse(MERGED, {"index", "about", "history"}))
    pages, _ = _prepare(tmp_path, "high", "site", planner)

    assert not planner.called
    assert [page.slug for page in pages] == ["index", "about", "history"]


def test_entry_mode_never_restructures(tmp_path: Path) -> None:
    planner = StubPlanner(SitePlanner._parse(MERGED, {"index", "about", "history"}))
    pages, _ = _prepare(tmp_path, "madmax", "entry", planner)

    assert not planner.called
    assert [page.slug for page in pages] == ["index"]


def test_a_failed_plan_falls_back_to_the_source_structure(tmp_path: Path) -> None:
    class Failing:
        def plan(self, summaries):
            raise SitePlanError("model unavailable")

    pages, _ = _prepare(tmp_path, "madmax", "site", Failing())
    assert [page.slug for page in pages] == ["index", "about", "history"]


def test_staging_directory_is_cleaned_up(tmp_path: Path) -> None:
    _, run_dir = _prepare(tmp_path, "madmax", "site", StubPlanner(SitePlanner._parse(MERGED, {"index", "about", "history"})))
    assert not (run_dir / ".source").exists()
