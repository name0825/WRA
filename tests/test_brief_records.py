from pathlib import Path

import pytest

from wra.core.brief import parse_brief_records
from wra.core.inputs import discover
from wra.core.models import FeatureModel, RecordSection
from wra.validator.tokens import all_source_text

BRIEF = """
design:
  must:
    - 아래 sections의 텍스트를 빠짐없이 사용
sections:
  - id: hero
    heading: Hello
    body: 직접 만들고 직접 운영합니다.
    cta:
      - label: GitHub
        href: https://github.com/name0825
  - id: skills
    heading: Skills
    groups:
      - name: Back-end
        items: [Node.js, PHP]
  - id: projects
    heading: Projects
    items:
      - name: WRA
        status: 개발 중
        stack: [Python, Ollama]
        github: https://github.com/name0825/WRA
        image: wra.png
        points:
          - 2단계 루프
  - id: awards
    heading: Awards
    items:
      - date: 2022.10
        title: 동상
      - date: 2021.11
        title: 은상
  - id: contact
    heading: Contact
    links:
      - label: Email
        value: admin@morirt.xyz
        href: "mailto:admin@morirt.xyz"
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    root = tmp_path / "input"
    root.mkdir()
    (root / name).write_text(text, encoding="utf-8")
    return root


def test_a_structured_brief_is_parsed_into_sections_of_records(tmp_path: Path) -> None:
    records = discover(_write(tmp_path, "brief.yaml", BRIEF)).records

    assert [section.id for section in records] == ["hero", "skills", "projects", "awards", "contact"]
    by_id = {record.id: record for section in records for record in section.records}
    assert by_id["awards.0"].fields == {"date": "2022.10", "title": "동상"}
    assert by_id["projects.0"].fields["github"] == "https://github.com/name0825/WRA"
    assert by_id["projects.0"].fields["stack"] == ["Python", "Ollama"]
    assert "image" not in by_id["projects.0"].fields
    assert by_id["skills.group0"].fields == {"name": "Back-end", "items": ["Node.js", "PHP"]}
    assert by_id["hero.body"].fields == {"body": "직접 만들고 직접 운영합니다."}
    assert by_id["hero.cta0"].fields["href"] == "https://github.com/name0825"
    assert by_id["contact.link0"].fields["value"] == "admin@morirt.xyz"
    assert next(section for section in records if section.id == "awards").heading == "Awards"


def test_brief_without_sections_or_markdown_headings_has_no_records(tmp_path: Path) -> None:
    assert discover(_write(tmp_path, "brief.md", "# 소개\n- 2021. 11. 동상\n")).records == []
    assert parse_brief_records([]) == []


PROFILE = """# 김민수 포트폴리오
- 깔끔한 디자인
- 모던한 느낌

## 기술 스택
- Node.js, TypeScript, PHP, Python
- 데이터베이스: MySQL, Redis

## 학력
- 2018. 03 - 2022. 02 OO대학교 컴퓨터공학과 졸업

## 수상
- 2022. 10 동상: 공모전 수상

## 디자인
- 어두운 배경

## 연락
이메일로 연락주세요.
- 이메일: admin@morirt.xyz
- GitHub: https://github.com/name0825
- 취급 품목: 절삭공구(인서트, 드릴, 앤드밀), 산업윤활유
"""


@pytest.mark.parametrize("name", ["profile.yaml", "profile.md", "profile.txt"])
def test_a_markdown_brief_gives_one_section_per_heading_and_a_record_per_bullet(tmp_path: Path, name: str) -> None:
    records = discover(_write(tmp_path, name, PROFILE)).records

    assert [section.heading for section in records] == ["기술 스택", "학력", "수상", "연락"]
    assert [section.id for section in records] == ["section1", "section2", "section3", "section4"]
    by_id = {record.id: record.fields for section in records for record in section.records}
    assert by_id["section1.0"] == {"items": ["Node.js", "TypeScript", "PHP", "Python"]}
    assert by_id["section1.1"] == {"데이터베이스": "MySQL, Redis"}
    assert by_id["section3.0"] == {"text": "2022. 10 동상: 공모전 수상"}
    assert by_id["section4.0"] == {"body": "이메일로 연락주세요."}
    assert by_id["section4.2"] == {"GitHub": "https://github.com/name0825"}
    assert by_id["section4.3"] == {"취급 품목": "절삭공구(인서트, 드릴, 앤드밀), 산업윤활유"}
    assert not any("깔끔한" in str(fields) or "어두운" in str(fields) for fields in by_id.values())


def test_every_list_item_the_detector_extracts_sits_in_a_markdown_record(tmp_path: Path) -> None:
    from wra.validator.brief_facts import _list_items

    bundle = discover(_write(tmp_path, "profile.md", PROFILE))
    flat = " ".join(value if isinstance(value, str) else " ".join(value) for section in bundle.records for record in section.records for value in record.fields.values())

    assert all(token in flat for item in _list_items(bundle.brief) for token in item.replace(",", " ").split())


def test_a_yaml_sections_brief_keeps_its_own_records_even_with_markdown_comments(tmp_path: Path) -> None:
    records = discover(_write(tmp_path, "brief.yaml", "## 수상\n# - 2021. 11. 동상\n" + BRIEF)).records

    assert [section.id for section in records] == ["hero", "skills", "projects", "awards", "contact"]


def test_the_creator_never_sees_records_in_the_brief_text(tmp_path: Path) -> None:
    bundle = discover(_write(tmp_path, "brief.yaml", BRIEF))
    assert bundle.records and BRIEF.strip() in bundle.brief


def test_records_are_part_of_the_source_text_and_vanish_from_the_dump_when_empty() -> None:
    features = FeatureModel()
    assert "records" not in features.model_dump()
    assert "records" not in features.model_dump_json()

    features.records = [RecordSection.model_validate({"id": "awards", "records": [{"id": "awards.0", "fields": {"date": "2022.12"}}]})]
    assert "2022.12" in all_source_text(features)
    assert FeatureModel.model_validate_json(features.model_dump_json()).records == features.records


def _prepared(tmp_path: Path, pages: int):
    import json

    from test_brief_directives import _folder
    from test_creator import ASSETS, DRAFT, ReplyClient, StubCreator, _config, _creator

    from wra.core.preparation import InputPreparer

    bundle = discover(_folder(tmp_path / "input", brief_yaml=BRIEF))
    draft = _creator(ReplyClient(json.dumps(dict(DRAFT, pages=DRAFT["pages"][:pages])))).create(BRIEF, ASSETS)
    return InputPreparer(config=_config("madmax"), analyzer=None, creator=StubCreator(draft)).prepare(bundle, tmp_path / "run")


def test_a_single_page_site_carries_the_records_on_its_features(tmp_path: Path) -> None:
    (page,) = _prepared(tmp_path, 1)

    assert [section.id for section in page.features.records][:2] == ["hero", "skills"]


def test_a_multi_page_site_carries_no_records(tmp_path: Path) -> None:
    assert all(page.features.records == [] for page in _prepared(tmp_path, 2))


def test_the_briefs_language_beats_whatever_the_creator_wrote(tmp_path: Path, monkeypatch) -> None:
    import json

    from test_brief_directives import _folder
    from test_creator import ASSETS, DRAFT, ReplyClient, StubCreator, _config, _creator

    from wra.core import preparation
    from wra.core.preparation import InputPreparer

    real = preparation.feature_model_for

    def english(draft, page, assets):
        features = real(draft, page, assets)
        features.metadata.language = "en"
        return features

    monkeypatch.setattr(preparation, "feature_model_for", english)
    bundle = discover(_folder(tmp_path / "input", brief_md=PROFILE))
    draft = _creator(ReplyClient(json.dumps(dict(DRAFT, pages=DRAFT["pages"][:1])))).create(PROFILE, ASSETS)

    (page,) = InputPreparer(config=_config("madmax"), analyzer=None, creator=StubCreator(draft)).prepare(bundle, tmp_path / "run")

    assert page.features.metadata.language == "ko"
