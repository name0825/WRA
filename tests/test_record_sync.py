import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_brief_records import BRIEF
from test_hill_climbing import _config, _evaluation, _FloorGenerator, _NoteDesigner, _ScriptedEvaluator

from wra.core.brief import parse_brief_records
from wra.core.iteration import IterationEngine
from wra.core.models import BriefRecord, FeatureModel, FrameworkConfig, RecordSection
from wra.core.preparation import PreparedPage
from wra.generator.generator import HTMLGenerator
from wra.generator.record_sync import sync_brief_records
from wra.refiner.refiner import HTMLRefiner
from wra.validator.brief_facts import find_dropped_brief_facts
from wra.validator.tokens import extract_visible_text

ROOT = Path(__file__).resolve().parent.parent
SKELETON = "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"></head><body><header>h</header><main><h1>X</h1></main><footer>f</footer></body></html>"


def _records(tmp_path: Path, text: str = BRIEF) -> list[RecordSection]:
    path = tmp_path / "brief.yaml"
    path.write_text(text, encoding="utf-8")
    return parse_brief_records([SimpleNamespace(kind="brief", relative="brief.yaml", path=path)])


def _awards(*dated: tuple[str, str], heading: str = "Awards") -> list[RecordSection]:
    records = [BriefRecord(id=f"awards.{index}", fields={"date": date, "title": title}) for index, (date, title) in enumerate(dated)]
    return [RecordSection(id="awards", heading=heading, records=records)]


def _page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><meta charset=\"UTF-8\"></head><body><main>{body}</main><footer>f</footer></body></html>"


@pytest.mark.parametrize("freedom", ["low", "high", "madmax"])
def test_an_empty_main_gets_every_brief_fact_and_the_sync_is_idempotent(tmp_path: Path, freedom: str) -> None:
    records = _records(tmp_path)
    page, repairs = sync_brief_records(SKELETON, records, freedom)

    assert repairs
    assert find_dropped_brief_facts(page, BRIEF, limit=1000) == []
    assert page.index("<section") > page.index("<main>") and page.index("</section>") < page.index("<footer>")
    again, second = sync_brief_records(page, records, freedom)
    assert again == page and second == []


def test_no_records_leaves_the_page_untouched() -> None:
    assert sync_brief_records(SKELETON, [], "low") == (SKELETON, [])


def test_a_tagged_date_is_overwritten_verbatim() -> None:
    page = _page('<ul><li data-wra-record="awards.0"><time data-wra-field="date">2021. 11.</time><span data-wra-field="title">동상</span></li></ul>')
    fixed, repairs = sync_brief_records(page, _awards(("2021.11", "동상")), "madmax")

    assert '<time data-wra-field="date">2021.11</time>' in fixed
    assert repairs == ["tagged: overwrote awards.0.date"]
    assert sync_brief_records(fixed, _awards(("2021.11", "동상")), "madmax")[0] == fixed


class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.ok = True

    def handle_starttag(self, tag, attrs):
        if tag not in {"br", "img", "meta", "hr", "input", "link"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.ok = False


def _balanced(html: str) -> bool:
    parser = _Balance()
    parser.feed(html)
    return parser.ok and not parser.stack


def test_a_tagged_field_with_child_markup_keeps_its_link_and_the_sync_is_idempotent() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"title": "Foo service", "github": "https://github.com/a/foo"})])]
    page = _page('<article data-wra-record="p.0"><h3 data-wra-field="title"><a href="https://github.com/a/foo">Foo (team)</a></h3></article>')
    fixed, _ = sync_brief_records(page, records, "low")

    assert "https://github.com/a/foo" in fixed
    assert "Foo service" in fixed
    assert _balanced(fixed)
    assert sync_brief_records(fixed, records, "low") == (fixed, [])


def test_nested_tagged_fields_do_not_overlap_and_lose_nothing() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"title": "Foo service", "org": "ACM Inc"})])]
    page = _page('<article data-wra-record="p.0"><h3 data-wra-field="title">Foo app <span data-wra-field="org">ACM Inc</span></h3></article>')
    fixed, _ = sync_brief_records(page, records, "low")

    assert "Foo service" in fixed
    assert '<span data-wra-field="org">ACM Inc</span>' in fixed
    assert _balanced(fixed)
    assert sync_brief_records(fixed, records, "low") == (fixed, [])


def test_a_missing_field_is_appended_inside_its_record() -> None:
    page = _page('<ul><li data-wra-record="awards.0"><span data-wra-field="title">동상</span></li><li>other</li></ul>')
    fixed, _ = sync_brief_records(page, _awards(("2022.12", "동상")), "high")

    assert '<span data-wra-field="title">동상</span><span data-wra-field="date">2022.12</span></li><li>other</li>' in fixed


def test_a_missing_list_item_joins_the_tagged_list_in_its_style() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"name": "WRA", "stack": ["Python", "Flask"]})])]
    page = _page('<article data-wra-record="p.0"><h3 data-wra-field="name">WRA</h3><ul data-wra-field="stack"><li class="tag">Python</li></ul></article>')
    fixed, _ = sync_brief_records(page, records, "low")

    assert '<li class="tag">Python</li><li class="tag">Flask</li></ul>' in fixed


def test_a_missing_item_beside_an_inline_tagged_field_becomes_a_sibling_element() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"stack": ["Node.js", "TS", "PHP", "Python"]})])]
    page = _page('<article data-wra-record="p.0"><span class="tech" data-wra-field="stack">Node.js</span></article>')
    fixed, _ = sync_brief_records(page, records, "low")

    assert '<article data-wra-record="p.0"><span class="tech" data-wra-field="stack">Node.js</span> <span class="tech" data-wra-field="stack">TS</span> <span class="tech" data-wra-field="stack">PHP</span> <span class="tech" data-wra-field="stack">Python</span></article>' in fixed
    assert "Node.jsTS" not in extract_visible_text(fixed)
    again, second = sync_brief_records(fixed, records, "low")
    assert again == fixed and second == []


def test_a_missing_item_beside_a_tagged_span_in_a_list_item_clones_the_list_item() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"stack": ["Node.js", "TS", "PHP", "Python"]})])]
    page = _page('<ul data-wra-record="p.0"><li><span data-wra-field="stack">Node.js</span></li></ul>')
    fixed, _ = sync_brief_records(page, records, "low")

    assert '<ul data-wra-record="p.0"><li><span data-wra-field="stack">Node.js</span></li><li><span data-wra-field="stack">TS</span></li><li><span data-wra-field="stack">PHP</span></li><li><span data-wra-field="stack">Python</span></li></ul>' in fixed
    again, second = sync_brief_records(fixed, records, "low")
    assert again == fixed and second == []


def test_a_missing_item_beside_a_field_of_an_untagged_record_list_item_stays_inside_the_record() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"name": "WRA Project", "stack": ["Node.js", "TS"]})])]
    page = _page('<ul><li><h3 data-wra-field="name">WRA Project</h3> <span data-wra-field="stack">Node.js</span></li></ul>')
    fixed, _ = sync_brief_records(page, records, "low")

    assert fixed.count(">TS<") == 1
    again, second = sync_brief_records(fixed, records, "low")
    assert again == fixed and second == []


def test_a_lost_record_is_cloned_from_its_tagged_sibling() -> None:
    page = _page('<div class="tl"><div class="row" data-wra-record="awards.0"><b class="d" data-wra-field="date">2022.12</b><i data-wra-field="title">A상</i></div></div>')
    fixed, repairs = sync_brief_records(page, _awards(("2022.12", "A상"), ("2021.11", "B상")), "high")

    assert '</div><div class="row" data-wra-record="awards.1"><b class="d" data-wra-field="date">2021.11</b><i data-wra-field="title">B상</i></div></div>' in fixed
    assert repairs == ["fallback: injected record awards.1"]


def test_an_untagged_section_is_found_by_its_heading() -> None:
    page = _page('<section class="x"><h2>Awards</h2><p>intro</p></section><section><h2>Other</h2></section>')
    fixed, _ = sync_brief_records(page, _awards(("2022.12", "A상")), "high")

    section = fixed[fixed.index('<section class="x">'):fixed.index("</section>")]
    assert "A상" in section and "2022.12" in section and fixed.count("<section") == 2


def test_a_lost_section_is_created_inside_main_before_the_footer() -> None:
    fixed, repairs = sync_brief_records(_page("<h1>X</h1><footer>in main</footer>"), _awards(("2022.12", "A상")), "high")

    assert fixed.index("<h2>Awards</h2>") < fixed.index("<footer>in main</footer>")
    assert fixed.index("<h2>Awards</h2>") > fixed.index("<main>")
    assert repairs == ["fallback: created section awards for record awards.0"]


def test_an_untagged_record_gets_its_missing_date_inside_its_own_element_and_is_tagged() -> None:
    page = _page("<ul><li>A상</li><li>B상 입상</li></ul>")
    fixed, repairs = sync_brief_records(page, _awards(("2022.12", "A상")), "madmax")

    assert '<li data-wra-record="awards.0">A상<span data-wra-field="date">2022.12</span></li>' in fixed
    assert repairs == ["fallback: appended awards.0.date"]
    assert sync_brief_records(fixed, _awards(("2022.12", "A상")), "madmax")[0] == fixed


def test_an_untagged_record_that_is_complete_is_left_byte_identical() -> None:
    page = _page("<ul><li>2022년 12월 A상</li></ul>")
    assert sync_brief_records(page, _awards(("2022.12", "A상")), "madmax") == (page, [])


def test_duplicate_record_ids_keep_the_first_and_all_content() -> None:
    page = _page('<p data-wra-record="awards.0">2022.12 A상</p><p data-wra-record="awards.0">2022.12 A상 again</p>')
    fixed, repairs = sync_brief_records(page, _awards(("2022.12", "A상")), "high")

    assert fixed.count("data-wra-record") == 1
    assert "A상 again" in fixed
    assert repairs == ["tagged: dropped duplicate record id awards.0"]


def test_prose_is_verbatim_at_low_and_token_checked_above_it() -> None:
    records = [RecordSection(id="p", records=[BriefRecord(id="p.0", fields={"name": "WRA", "summary": "로컬 모델로 디자인을 만든다"})])]
    reworded = _page('<article data-wra-record="p.0"><h3 data-wra-field="name">WRA</h3><p data-wra-field="summary">디자인을 자동으로 생성</p></article>')

    kept, _ = sync_brief_records(reworded, records, "madmax")
    assert kept == reworded
    low, _ = sync_brief_records(reworded, records, "low")
    assert "로컬 모델로 디자인을 만든다" in low

    lost = _page('<article data-wra-record="p.0"><h3 data-wra-field="name">WRA</h3></article>')
    restored, _ = sync_brief_records(lost, records, "madmax")
    assert '<p data-wra-field="summary">로컬 모델로 디자인을 만든다</p>' in restored


def test_malformed_html_does_not_break_the_sync() -> None:
    page = "<html><body><main><ul><li>A상<li>B상</main>"
    fixed, _ = sync_brief_records(page, _awards(("2022.12", "A상")), "high")

    assert "2022.12" in fixed
    assert sync_brief_records(fixed, _awards(("2022.12", "A상")), "high")[0] == fixed


def _text_generator(tmp_path: Path, html: str, freedom: str = "high") -> HTMLGenerator:
    from test_generator import FakeLLMClient

    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    return HTMLGenerator(FakeLLMClient(html), "generator-model", FrameworkConfig(), prompt, content_freedom=freedom)


FULL_HEAD = '<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
LOSSY = f"<!DOCTYPE html><html lang=\"ko\">{FULL_HEAD}<body><main><h1>X</h1></main></body></html>"


def test_the_generator_restores_a_record_the_model_dropped_and_logs_it(tmp_path: Path, caplog) -> None:
    from test_generator import make_spec

    features = FeatureModel(records=_awards(("2022.12", "A상")))
    with caplog.at_level(logging.INFO, logger="wra.generator.generator"):
        html = _text_generator(tmp_path, LOSSY).generate(features, make_spec())

    assert 'data-wra-record="awards.0"' in html and "2022.12" in html and "A상" in html
    assert any("Synced brief records" in record.message for record in caplog.records)


def test_a_page_without_records_comes_out_exactly_as_before(tmp_path: Path) -> None:
    from test_generator import make_spec

    generated = _text_generator(tmp_path, LOSSY).generate(FeatureModel(), make_spec())

    assert re.sub(r'<style id="wra-theme"[^>]*>.*?</style>\n?', "", generated, flags=re.DOTALL) == LOSSY


def test_the_generator_prompt_carries_the_records_and_the_slot_rule(tmp_path: Path) -> None:
    from test_generator import make_spec

    generator = _text_generator(tmp_path, LOSSY)
    generator.generate(FeatureModel(records=_awards(("2022.12", "A상"))), make_spec())
    prompt = generator._llm_client.calls[0]["prompt"]
    assert '"records"' in prompt and "2022.12" in prompt

    generator.generate(FeatureModel(), make_spec())
    assert '"records"' not in generator._llm_client.calls[1]["prompt"]
    system = (ROOT / "prompts" / "generator" / "system.md").read_text(encoding="utf-8")
    assert "data-wra-record" in system and "data-wra-field" in system and "never drop or merge a record" in system


def test_the_refiner_restores_a_record_the_model_dropped(monkeypatch) -> None:
    from test_refiner import PAGE, PROMPT, ReplyClient, SPEC, _evaluation as refiner_evaluation

    features = FeatureModel(records=_awards(("2022.12", "A상")))
    refined = HTMLRefiner(llm_client=ReplyClient(PAGE), model="test", prompt_path=PROMPT, content_freedom="high").refine(features, PAGE, refiner_evaluation(), design_spec=SPEC)

    assert 'data-wra-record="awards.0"' in refined and "2022.12" in refined
    assert "data-wra-record" in PROMPT.read_text(encoding="utf-8")


def _gate_engine(tmp_path: Path, monkeypatch, brief: str, html: str):
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features, content_freedom="low": SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features, content_freedom="low": [])
    config = _config(patience=0)
    pipeline = config.pipeline.model_copy(update={
        "max_iterations": 1, "evaluation_threshold": 99.0,
        "refine": config.pipeline.refine.model_copy(update={"enabled": False}),
        "output": config.pipeline.output.model_copy(update={"save_mode": "best"}),
    })
    config = config.model_copy(update={"pipeline": pipeline, "content": config.content.model_copy(update={"freedom": "high"}), "browser": config.browser.model_copy(update={"enabled": False})})
    engine = IterationEngine(config=config, designer=_NoteDesigner(), generator=_FloorGenerator([html]), evaluator=_ScriptedEvaluator([_evaluation(8.0)]))
    features = FeatureModel(records=_records(tmp_path, brief))
    page = PreparedPage(slug="index", relative="index.html", html="", features=features, run_root=tmp_path, data_dir=tmp_path / "data" / "index", is_entry=True, created=True, brief=brief)
    return engine.run_pages([page])


def test_the_floor_gate_syncs_the_records_before_publishing_a_page_that_still_misses_facts(tmp_path: Path, monkeypatch) -> None:
    import json

    brief = "sections:\n  - id: services\n    heading: Services\n    items:\n      - name: Alpha Zeta\n      - name: Bravo Yankee\n"
    results = _gate_engine(tmp_path, monkeypatch, brief, "<html><body><main><p>Alpha Zeta</p></main></body></html>")

    assert "Bravo Yankee" in results[0].html
    assert results[0].floor_violations == []
    assert "Bravo Yankee" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["floor_met"] is True


def test_the_design_constraints_name_every_record_section(tmp_path: Path) -> None:
    from test_hill_climbing import _engine

    page = SimpleNamespace(features=FeatureModel(records=_awards(("2022.12", "A상"))), content_focus="", created=True, directives=None)
    constraints = _engine(_config())._build_design_constraints(1, None, page)
    assert constraints["brief_records"]["sections"] == [{"id": "awards", "heading": "Awards", "records": 1}]

    page.features = FeatureModel()
    assert "brief_records" not in _engine(_config())._build_design_constraints(1, None, page)


def test_markdown_records_fill_an_empty_main_with_every_fact_and_stay_idempotent(tmp_path: Path) -> None:
    from test_brief_records import PROFILE

    path = tmp_path / "profile.md"
    path.write_text(PROFILE, encoding="utf-8")
    records = parse_brief_records([SimpleNamespace(kind="brief", relative="profile.md", path=path)])

    page, repairs = sync_brief_records(SKELETON, records, "madmax")

    assert repairs
    assert find_dropped_brief_facts(page, PROFILE, limit=1000) == []
    again, second = sync_brief_records(page, records, "madmax")
    assert again == page and second == []
