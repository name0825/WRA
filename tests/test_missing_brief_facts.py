from pathlib import Path
from types import SimpleNamespace

from test_generator import make_spec
from test_hill_climbing import _config, _evaluation, _HtmlGenerator
from test_iteration_brief import _RecordingEvaluator, _SpecDesigner, _page
from test_refiner import BETTER, PAGE, ReplyClient, _evaluation as _refiner_evaluation, _refiner
from wra.core.iteration import IterationEngine, collect_fact_loss_findings, collect_missing_brief_facts
from wra.core.models import FeatureModel
from wra.generator.generator import HTMLGenerator
from wra.validator.brief_facts import MISSING_BRIEF_FACT_PREFIX, find_dropped_brief_facts, locate_brief_facts

BRIEF = """sections:
  - id: education
    items:
      - date: 2026.03 ~
        title: 영남대학교 정보통신공학과 복학
      - date: 2023.03
        title: 영남대학교 정보통신공학과 입학
  - id: projects
    items:
      - name: Home Lab
        stack: [Proxmox, Tailscale]
        link: https://github.com/name0825/homelab
"""
EMPTY_PAGE = "<html><body><main><p>nothing here</p></main></body></html>"
ENTRY = "- date: 2026.03 ~ | title: 영남대학교 정보통신공학과 복학"


def test_a_dropped_date_carries_the_whole_brief_entry_it_came_from() -> None:
    located = locate_brief_facts(BRIEF, find_dropped_brief_facts(EMPTY_PAGE, BRIEF))

    by_fact = {item["fact"]: item["source_line"] for item in located}
    assert by_fact["2026.03"] == ENTRY
    assert by_fact["2023.03"] == "- date: 2023.03 | title: 영남대학교 정보통신공학과 입학"
    assert "https://github.com/name0825/homelab" in by_fact["https://github.com/name0825/homelab"]
    assert "Proxmox" in by_fact["Proxmox"]
    assert "Home Lab" in by_fact["Proxmox"]


def test_a_spaced_date_in_the_brief_is_located() -> None:
    brief = "## 경력\n- 2022. 03 입사\n"

    located = locate_brief_facts(brief, [f"{MISSING_BRIEF_FACT_PREFIX}: date 2022.03"])

    assert located == [{"fact": "2022.03", "source_line": "- 2022. 03 입사"}]


def test_findings_that_are_not_brief_facts_are_ignored() -> None:
    assert locate_brief_facts(BRIEF, ["corrupted source token: 'A' rendered as 'B'"]) == []


def test_the_scoring_list_is_still_capped_while_the_refiner_list_is_not(tmp_path: Path) -> None:
    brief = "\n".join(f"- Item{chr(97 + index)}{chr(97 + index)}x" for index in range(20))

    assert len(find_dropped_brief_facts(EMPTY_PAGE, brief)) == 12
    assert len(find_dropped_brief_facts(EMPTY_PAGE, brief, limit=40)) == 20
    page = _page(tmp_path, brief=brief)
    assert len(collect_fact_loss_findings(page, EMPTY_PAGE, "madmax")) == 12
    assert len(collect_missing_brief_facts(page, EMPTY_PAGE, "madmax")) == 20


def test_no_facts_are_collected_where_coverage_is_enforced_or_the_page_is_not_a_brief_entry(tmp_path: Path) -> None:
    assert collect_missing_brief_facts(_page(tmp_path, brief=BRIEF), EMPTY_PAGE, "low") == []
    assert collect_missing_brief_facts(_page(tmp_path, brief=BRIEF, is_entry=False), EMPTY_PAGE, "madmax") == []
    assert collect_missing_brief_facts(_page(tmp_path, brief=BRIEF, created=False), EMPTY_PAGE, "madmax") == []


def test_the_refiner_prompt_carries_the_verbatim_source_lines() -> None:
    client = ReplyClient(BETTER)
    facts = locate_brief_facts(BRIEF, find_dropped_brief_facts(EMPTY_PAGE, BRIEF))

    _refiner(client).refine(FeatureModel(), PAGE, _refiner_evaluation(), missing_brief_facts=facts)

    prompt = client.prompts[0]
    assert "missing_brief_facts:" in prompt
    assert ENTRY in prompt
    assert "`missing_brief_facts`" in prompt


def test_the_refiner_prompt_has_no_block_without_missing_facts() -> None:
    client = ReplyClient(BETTER)

    _refiner(client).refine(FeatureModel(), PAGE, _refiner_evaluation())

    assert "missing_brief_facts:\n" not in client.prompts[0]


def test_the_generator_prompt_carries_the_verbatim_source_lines() -> None:
    facts = locate_brief_facts(BRIEF, find_dropped_brief_facts(EMPTY_PAGE, BRIEF))

    prompt = HTMLGenerator._build_prompt("TEMPLATE", FeatureModel(), make_spec(), [], "", None, True, facts)

    assert "restore every one verbatim" in prompt
    assert ENTRY in prompt
    assert "restore every one verbatim" not in HTMLGenerator._build_prompt("TEMPLATE", FeatureModel(), make_spec(), [], "", None, True, None)


class _SpyGenerator(_HtmlGenerator):
    def __init__(self, pages) -> None:
        super().__init__(pages)
        self.facts: list = []

    def generate(self, features, design_spec, generation_rules, model, think=None, timeout=None, asset_root=None, preserve_facts=False, missing_brief_facts=None):
        self.facts.append(missing_brief_facts)
        return super().generate(features, design_spec, generation_rules, model, think, timeout, asset_root, preserve_facts)


def test_a_redesign_hands_the_generator_what_the_best_page_dropped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features, content_freedom="low": SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features, content_freedom="low": [])
    config = _config(patience=0)
    config = config.model_copy(update={
        "pipeline": config.pipeline.model_copy(update={"max_iterations": 2, "evaluation_threshold": 99.0, "refine": config.pipeline.refine.model_copy(update={"enabled": False}), "output": config.pipeline.output.model_copy(update={"save_mode": "best"})}),
        "content": config.content.model_copy(update={"freedom": "madmax"}),
    })
    generator = _SpyGenerator([EMPTY_PAGE])
    engine = IterationEngine(config=config, designer=_SpecDesigner(), generator=generator, evaluator=_RecordingEvaluator([_evaluation(7.0), _evaluation(7.0)]))

    engine.run_pages([_page(tmp_path, brief=BRIEF)])

    assert generator.facts[0] is None
    assert any(item["source_line"] == ENTRY for item in generator.facts[1])
