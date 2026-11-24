import json
from dataclasses import replace
from pathlib import Path

from test_creator import ASSETS, BRIEF, DRAFT, ReplyClient, StubCreator, _config, _creator

from wra.core.brief import BriefDirectives, parse_brief_directives
from wra.core.inputs import discover
from wra.core.preparation import InputPreparer

STRUCTURED = """
site:
  logo: site-logo.svg
design:
  palette:
    background: "#0F172A"
    primary: "#60A5FA"
    accent: navy
  typography:
    heading: Pretendard
    mono: JetBrains Mono
  layout:
    - 상단 고정 내비게이션
  must:
    - 다크 모드 기본
  avoid:
    - 스톡 사진
nav: [About, Skills, Contact]
sections:
  - id: hero
"""


def _folder(root: Path, **files: str) -> Path:
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name.replace("_", ".", 1)).write_text(text, encoding="utf-8")
    return root


def test_a_structured_brief_yields_its_directives(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_yaml=STRUCTURED)
    directives = discover(root).directives
    assert directives == BriefDirectives(
        palette={"background": "#0F172A", "primary": "#60A5FA"},
        fonts={"heading": "Pretendard", "mono": "JetBrains Mono"},
        logo="site-logo.svg",
        layout=["상단 고정 내비게이션"],
        must=["다크 모드 기본"],
        avoid=["스톡 사진"],
        nav=["About", "Skills", "Contact"],
    )


def test_the_design_logo_wins_over_the_site_logo(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_yaml="site:\n  logo: a.svg\ndesign:\n  logo: b.svg\n")
    assert discover(root).directives.logo == "b.svg"


def test_the_raw_brief_text_still_reaches_the_creator_unchanged(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_yaml=STRUCTURED)
    assert STRUCTURED.strip() in discover(root).brief


def test_malformed_yaml_falls_back_to_the_text(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_yaml="design: [unclosed\n  palette: {")
    bundle = discover(root)
    assert bundle.directives is None
    assert "design: [unclosed" in bundle.brief


def test_markdown_and_plain_yaml_without_directives_give_none(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_md="# 소개\n- 팔레트: #112233\n", notes_yaml="company: 대성정밀\nfounded: 1998\n")
    assert discover(root).directives is None
    assert parse_brief_directives([]) is None


def test_a_created_site_takes_its_language_from_the_brief(tmp_path: Path) -> None:
    bundle = discover(_folder(tmp_path / "input", brief_md=BRIEF))
    draft = _creator(ReplyClient(json.dumps(DRAFT))).create(BRIEF, ASSETS)

    pages = InputPreparer(config=_config("madmax"), analyzer=None, creator=StubCreator(draft)).prepare(bundle, tmp_path / "run")

    assert all(page.features.metadata.language == "ko" for page in pages)


def _prepared(tmp_path: Path, logo_file: str | None, brief_yaml: str = STRUCTURED):
    root = _folder(tmp_path / "input", brief_md=BRIEF, brief_yaml=brief_yaml)
    if logo_file:
        target = root / logo_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    bundle = discover(root)
    draft = _creator(ReplyClient(json.dumps(DRAFT))).create(BRIEF, ASSETS)
    return bundle, InputPreparer(config=_config("madmax"), analyzer=None, creator=StubCreator(draft)).prepare(bundle, tmp_path / "run")


def test_a_created_site_carries_the_directives_to_every_page(tmp_path: Path) -> None:
    bundle, pages = _prepared(tmp_path, "site-logo.svg")

    assert bundle.directives is not None
    assert all(page.directives == replace(bundle.directives, logo="assets/site-logo.svg") for page in pages)
    assert len(pages) == 2
    assert all(page.brief == "" for page in pages)


def test_the_logo_is_resolved_to_its_output_path_by_full_key_then_basename(tmp_path: Path) -> None:
    _, pages = _prepared(tmp_path / "a", "img/brand.svg", STRUCTURED.replace("site-logo.svg", "img/brand.svg"))
    assert pages[0].directives.logo == "assets/brand.svg"
    _, pages = _prepared(tmp_path / "b", "img/brand.svg", STRUCTURED.replace("site-logo.svg", "/static/brand.svg"))
    assert pages[0].directives.logo == "assets/brand.svg"


def test_a_logo_that_was_not_supplied_is_dropped_but_other_directives_stay(tmp_path: Path) -> None:
    _, pages = _prepared(tmp_path, None)
    assert pages[0].directives.logo is None
    assert pages[0].directives.palette


def test_an_external_logo_url_is_left_as_is(tmp_path: Path) -> None:
    _, pages = _prepared(tmp_path, None, STRUCTURED.replace("site-logo.svg", "https://example.com/logo.svg"))
    assert pages[0].directives.logo == "https://example.com/logo.svg"


def test_yaml_that_fails_to_parse_with_any_error_is_skipped(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_md=BRIEF, notes_yaml="opened: 2024-02-30\n")
    bundle = discover(root)
    assert bundle.directives is None
    assert BRIEF.strip() in bundle.brief


def test_a_single_page_site_hands_its_brief_to_the_entry_page(tmp_path: Path) -> None:
    root = _folder(tmp_path / "input", brief_md=BRIEF)
    bundle = discover(root)
    single = dict(DRAFT, pages=DRAFT["pages"][:1])
    draft = _creator(ReplyClient(json.dumps(single))).create(BRIEF, ASSETS)

    pages = InputPreparer(config=_config("madmax"), analyzer=None, creator=StubCreator(draft)).prepare(bundle, tmp_path / "run")

    assert len(pages) == 1
    assert pages[0].brief == bundle.brief
    assert pages[0].directives is None
