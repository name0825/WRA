from pathlib import Path
from types import SimpleNamespace

from test_generator import make_spec
from test_hill_climbing import _config, _evaluation, _NoteDesigner, _HtmlGenerator, _ScriptedEvaluator
from wra.core.brief import BriefDirectives
from wra.core.iteration import IterationEngine, _apply_user_design, collect_fact_loss_findings
from wra.core.models import FeatureModel
from wra.core.preparation import PreparedPage
from wra.validator.adherence import DIRECTIVE_PREFIX
from wra.validator.brief_facts import MISSING_BRIEF_FACT_PREFIX
from wra.validator.language import find_language_drift

BRIEF = "# Profile\n\n## Stack\n\n- Fastify\n- PostgreSQL\n"
DIRECTIVES = BriefDirectives(palette={"background": "#112233", "primary": "#445566", "muted": "#778899"}, fonts={"heading": "Pretendard", "body": "Noto Sans KR"}, logo="assets/logo.svg", nav=["소개"])


def _page(tmp_path: Path, *, created=True, is_entry=True, brief=BRIEF, directives=None, language=None) -> PreparedPage:
    features = FeatureModel()
    features.metadata.language = language
    return PreparedPage(slug="index", relative="index.html", html="", features=features, run_root=tmp_path, data_dir=tmp_path / "data" / "index",
                        is_entry=is_entry, created=created, brief=brief, directives=directives)


def test_created_entry_page_reports_dropped_brief_facts_and_only_then(tmp_path: Path) -> None:
    page = _page(tmp_path)

    findings = collect_fact_loss_findings(page, "<p>PostgreSQL</p>", "madmax")
    assert findings and all(item.startswith(MISSING_BRIEF_FACT_PREFIX) for item in findings)
    assert any("Fastify" in item for item in findings)
    assert collect_fact_loss_findings(page, "<p>Fastify and PostgreSQL</p>", "madmax") == []


def test_brief_facts_are_not_checked_for_siblings_briefless_pages_or_enforced_coverage(tmp_path: Path) -> None:
    assert collect_fact_loss_findings(_page(tmp_path, is_entry=False), "<p>x</p>", "madmax") == []
    assert collect_fact_loss_findings(_page(tmp_path, brief=""), "<p>x</p>", "madmax") == []
    assert collect_fact_loss_findings(_page(tmp_path), "<p>x</p>", "low") == []


def _engine() -> IterationEngine:
    return IterationEngine(config=_config(), designer=None, generator=None, evaluator=None)


def test_user_design_constraint_is_set_even_in_auto_mode(tmp_path: Path) -> None:
    engine = _engine()
    assert engine._config.design.mode == "auto"

    constraints = engine._build_design_constraints(1, page=_page(tmp_path, directives=DIRECTIVES))

    assert constraints["user_design"]["palette"] == DIRECTIVES.palette
    assert constraints["user_design"]["typography"] == DIRECTIVES.fonts
    assert constraints["user_design"]["logo"] == "assets/logo.svg"
    assert "user_design" not in engine._build_design_constraints(1, page=_page(tmp_path))
    assert any("assets/logo.svg" in rule for rule in engine._build_generation_rules(_page(tmp_path, directives=DIRECTIVES)))
    assert not any("logo" in rule.lower() for rule in engine._build_generation_rules(_page(tmp_path)))


def test_apply_user_design_overwrites_what_the_designer_chose() -> None:
    spec = _apply_user_design(make_spec(), DIRECTIVES)

    colors = spec.design.color_system
    assert (colors.background, colors.primary, colors.muted_text) == ("#112233", "#445566", "#778899")
    assert colors.surface == make_spec().design.color_system.surface
    assert (spec.design.typography.heading, spec.design.typography.body) == ("Pretendard", "Noto Sans KR")
    assert _apply_user_design(make_spec(), BriefDirectives(nav=["a"])).design == make_spec().design


class _RecordingEvaluator(_ScriptedEvaluator):
    def __init__(self, evaluations) -> None:
        super().__init__(evaluations)
        self.visual: list[list[str]] = []
        self.fact_loss: list[list[str]] = []

    def evaluate(self, features, design_spec, html, browser_result, content_findings, ux_findings=None, visual_findings=None, model=None, think=None, timeout=None, fact_loss_findings=None):
        self.visual.append(list(visual_findings or []))
        self.fact_loss.append(list(fact_loss_findings or []))
        return super().evaluate(features, design_spec, html, browser_result, content_findings, ux_findings, visual_findings, model, think, timeout, fact_loss_findings)


class _SpecDesigner(_NoteDesigner):
    def plan(self, features, design_constraints, model, think=None, timeout=None, allow_forms=True):
        self.constraints.append(design_constraints)
        return make_spec()


def test_directive_language_and_brief_fact_findings_reach_the_evaluator_and_the_spec_is_enforced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features, content_freedom="low": SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features, content_freedom="low": [])
    config = _config(patience=0)
    config = config.model_copy(update={
        "pipeline": config.pipeline.model_copy(update={"max_iterations": 1, "evaluation_threshold": 99.0, "refine": config.pipeline.refine.model_copy(update={"enabled": False}), "output": config.pipeline.output.model_copy(update={"save_mode": "best"})}),
        "content": config.content.model_copy(update={"freedom": "madmax"}),
    })
    english = "<html><body><main><p>" + "This page is written entirely in English prose. " * 12 + "</p></main></body></html>"
    designer, evaluator, generator = _SpecDesigner(), _RecordingEvaluator([_evaluation(7.0)]), _HtmlGenerator([english])
    engine = IterationEngine(config=config, designer=designer, generator=generator, evaluator=evaluator)

    engine.run_pages([_page(tmp_path, directives=DIRECTIVES, language="ko")])

    assert designer.constraints[0]["user_design"]["palette"] == DIRECTIVES.palette
    visual = evaluator.visual[0]
    assert any(item.startswith(DIRECTIVE_PREFIX) and "#112233" in item for item in visual)
    assert any(item == find_language_drift(english, "ko")[0] for item in visual)
    assert any(MISSING_BRIEF_FACT_PREFIX in item and "Fastify" in item for item in evaluator.fact_loss[0])


def test_a_locked_language_reaches_the_rules_the_constraints_and_the_floor_gate(tmp_path: Path) -> None:
    from wra.core.iteration import collect_floor_violations

    engine = _engine()
    korean = _page(tmp_path, language="ko")

    rules = engine._build_generation_rules(korean)
    assert any("fixed to `ko`" in rule and "never translate the brief" in rule for rule in rules)
    assert "fixed to `ko`" in engine._build_design_constraints(1, page=korean)["output_language"]

    assert not any("fixed to" in rule for rule in engine._build_generation_rules(_page(tmp_path)))
    assert not any("fixed to" in rule for rule in engine._build_generation_rules(_page(tmp_path, created=False, language="ko")))
    assert "output_language" not in engine._build_design_constraints(1, page=_page(tmp_path))

    english = "<html lang=\"en\"><body><main>" + "<p>This portfolio presents a developer and the work that developer has shipped.</p>" * 8 + "</main></body></html>"
    assert any("language" in finding for finding in collect_floor_violations(korean, english, "madmax"))
    assert not any("language" in finding for finding in collect_floor_violations(_page(tmp_path, language="en"), english, "madmax"))


def test_the_records_constraint_forbids_invented_sections(tmp_path: Path) -> None:
    from wra.core.models import RecordSection

    page = _page(tmp_path)
    page.features.records = [RecordSection(id="section1", heading="수상")]

    instruction = _engine()._build_design_constraints(1, page=page)["brief_records"]["instruction"]

    assert "only content sections" in instruction and "invent no job title" in instruction
