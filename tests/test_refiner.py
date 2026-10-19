"""Refining the best page in place, instead of redesigning it every iteration."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from wra.core.iteration import IterationEngine, _PageRun
from wra.core.models import EvaluationResult, FeatureModel, WRAConfig
from wra.core.preparation import PreparedPage
from wra.refiner.refiner import HTMLRefiner, RefinerError
from support import sample_config_data


@pytest.fixture(autouse=True)
def _skip_contrast_repair(monkeypatch):
    monkeypatch.setattr("wra.refiner.refiner.repair_low_contrast", lambda html, asset_root=None: (html, []))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT = PROJECT_ROOT / "prompts" / "refiner" / "system.md"

PAGE = "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\"><title>T</title></head><body><main><h1>제목</h1><p>본문</p></main></body></html>"
BETTER = PAGE.replace("본문", "고쳐진 본문")

DIMENSIONS = ("design_completeness", "visual_hierarchy", "design_consistency", "content_preservation",
              "ux", "readability", "responsive_design", "originality", "html_quality")

SPEC = SimpleNamespace(model_dump_json=lambda: '{"design": {"color_system": {"primary": "#111"}}}')


def _evaluation(**scores) -> EvaluationResult:
    payload = {name: {"score": scores.get(name, 9.0), "feedback": f"{name} note"} for name in DIMENSIONS}
    payload["overall_score"] = 0.0
    payload["improvement_suggestions"] = ["대비를 높일 것"]
    return EvaluationResult.model_validate(payload)


class ReplyClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, prompt, model, options=None, images=None, *, think=None, timeout=None):
        self.calls += 1
        self.prompts.append(prompt)
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _refiner(client, **kwargs) -> HTMLRefiner:
    return HTMLRefiner(llm_client=client, model="test", prompt_path=PROMPT, **kwargs)


def test_a_valid_edit_is_returned(monkeypatch) -> None:
    refined = _refiner(ReplyClient(BETTER)).refine(FeatureModel(), PAGE, _evaluation())
    assert "고쳐진 본문" in refined


def test_the_refiner_protects_and_restores_contact_literal() -> None:
    page_with_email = PAGE.replace("본문", "a@example.com")
    response = page_with_email.replace("a@example.com", "WRAREF1")
    client = ReplyClient(response)
    features = FeatureModel(content={"paragraphs": ["Contact us at a@example.com."]})

    refined = _refiner(client).refine(features, page_with_email, _evaluation())

    assert "WRAREF" in client.prompts[0]
    assert "a@example.com" not in client.prompts[0]
    assert "a@example.com" in refined
    assert "WRAREF" not in refined


def test_the_prompt_carries_the_page_and_its_weaknesses() -> None:
    client = ReplyClient(BETTER)
    _refiner(client, weakest=2).refine(FeatureModel(), PAGE, _evaluation(ux=4.0, readability=5.0), content_findings=["corrupted source token: 'A' rendered as 'B'"])
    prompt = client.prompts[0]

    assert PAGE in prompt, "the refiner edits the page it is given"
    assert "ux" in prompt and "readability" in prompt
    assert "대비를 높일 것" in prompt
    # The raw finding is replaced by a located, mechanical replacement.
    assert "content_repairs" in prompt
    assert '"replace":"B"' in prompt


def test_refiner_prompt_includes_local_mobile_overflow_repair_rule() -> None:
    client = ReplyClient(BETTER)
    evaluation = _evaluation(html_quality=4.0)
    evaluation.html_quality.feedback = "Mobile horizontal_overflow at width 390: scroll_width 511."

    _refiner(client).refine(FeatureModel(), PAGE, evaluation)

    prompt = client.prompts[0]
    assert "Mobile horizontal_overflow at width 390: scroll_width 511." in prompt
    assert "repair that concrete defect locally" in prompt
    assert "Do not hide horizontal overflow on\n`html` or `body`." in prompt


def test_a_corruption_is_turned_into_a_located_replacement() -> None:
    """A finding names the wrong word; the refiner must be told where it sits."""
    html = '<title>취업품목 | T</title><body><h1>취업품목</h1></body>'
    repairs = HTMLRefiner.content_repairs(html, ["corrupted source token: '취급품목' rendered as '취업품목'"])

    assert len(repairs) == 1
    assert repairs[0]["replace"] == "취업품목"
    assert repairs[0]["with"] == "취급품목"
    assert len(repairs[0]["occurrences"]) == 2, "both places, including the one in <head>"
    assert "<title>" in repairs[0]["occurrences"][0]


def test_a_corruption_that_is_no_longer_present_says_so() -> None:
    repairs = HTMLRefiner.content_repairs(PAGE, ["corrupted source token: 'A' rendered as 'B'"])
    assert repairs[0]["occurrences"] == ["not found in the current page"]


def test_an_unrecognised_finding_is_passed_through() -> None:
    repairs = HTMLRefiner.content_repairs(PAGE, ["something else entirely"])
    assert repairs == [{"note": "something else entirely"}]


def test_the_repairs_reach_the_prompt_with_their_context() -> None:
    client = ReplyClient(BETTER)
    page = PAGE.replace("제목", "취업품목")
    _refiner(client).refine(FeatureModel(), page, _evaluation(), content_findings=["corrupted source token: '취급품목' rendered as '취업품목'"])
    prompt = client.prompts[0]
    assert "content_repairs" in prompt
    assert '"with":"취급품목"' in prompt


def test_only_the_weakest_dimensions_are_targeted() -> None:
    refiner = _refiner(ReplyClient(BETTER), weakest=2)
    weakest = refiner.weakest_dimensions(_evaluation(ux=4.0, readability=5.0, originality=6.0))
    assert [name for name, _, _ in weakest] == ["ux", "readability"]


def test_a_fenced_response_is_unwrapped() -> None:
    refined = _refiner(ReplyClient(f"```html\n{BETTER}\n```")).refine(FeatureModel(), PAGE, _evaluation())
    assert refined.startswith("<!DOCTYPE html")


def test_commentary_around_the_document_is_dropped() -> None:
    noisy = f"Here are my edits.\n\n{BETTER}\n\nI improved the contrast."
    refined = _refiner(ReplyClient(noisy)).refine(FeatureModel(), PAGE, _evaluation())
    assert refined.endswith("</html>")
    assert "I improved" not in refined


def test_an_invalid_edit_is_retried_then_refused() -> None:
    client = ReplyClient("<p>fragment only</p>")
    with pytest.raises(RefinerError):
        _refiner(client, max_retries=1).refine(FeatureModel(), PAGE, _evaluation())
    assert client.calls == 2


def test_a_retry_recovers_from_a_bad_first_response() -> None:
    client = ReplyClient("<p>fragment only</p>", BETTER)
    refined = _refiner(client, max_retries=2).refine(FeatureModel(), PAGE, _evaluation())
    assert "고쳐진 본문" in refined
    assert client.calls == 2


# --- when the engine refines instead of redesigning -------------------------

def _config(**refine) -> WRAConfig:
    data = sample_config_data()
    data["pipeline"] = {**data["pipeline"], "refine": {"enabled": True, **refine}}
    return WRAConfig.model_validate(data)


def _state(best_score: float | None = 8.0, stalled: int = 0) -> _PageRun:
    page = PreparedPage(slug="index", relative="index.html", html="", features=FeatureModel(),
                        run_root=Path("."), data_dir=Path("."), is_entry=True)
    state = _PageRun(page=page, stalled=stalled)
    if best_score is not None:
        state.best = SimpleNamespace(evaluation=_evaluation(readability=7.0), html=PAGE, design_spec=SPEC, iteration=1)
    return state


def _engine(config: WRAConfig, refiner=object()) -> IterationEngine:
    return IterationEngine(config=config, designer=None, generator=None, evaluator=None, refiner=refiner)


def test_the_first_iteration_cannot_refine() -> None:
    """There is nothing to improve until something has been scored."""
    assert not _engine(_config())._refines(_state(best_score=None))


def test_a_scored_page_is_refined() -> None:
    assert _engine(_config())._refines(_state())


def test_refining_is_off_by_default() -> None:
    data = sample_config_data()
    assert not WRAConfig.model_validate(data).pipeline.refine.enabled


def test_no_refiner_means_no_refining() -> None:
    assert not _engine(_config(), refiner=None)._refines(_state())


def test_a_stalled_page_is_redesigned_instead() -> None:
    """Several small edits that moved nothing mean the design is the problem."""
    engine = _engine(_config(redesign_after_stalls=2))
    assert engine._refines(_state(stalled=1))
    assert not engine._refines(_state(stalled=2))


def test_zero_stalls_never_falls_back() -> None:
    engine = _engine(_config(redesign_after_stalls=0))
    assert engine._refines(_state(stalled=9))


# --- the engine actually uses the refiner instead of rebuilding --------------

class _Designer:
    def __init__(self) -> None:
        self.plans = 0
        self.revisions = 0

    def plan(self, features, design_constraints, model, think=None, timeout=None):
        self.plans += 1
        return SimpleNamespace(sections=[])

    def revise(self, features, previous_design_spec, previous_html, evaluation, design_constraints, model, think=None, timeout=None):
        self.revisions += 1
        return SimpleNamespace(sections=[])


class _Generator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, features, design_spec, generation_rules, model, think=None, timeout=None, asset_root=None):
        self.calls += 1
        return PAGE.replace("본문", f"생성 {self.calls}")


class _Evaluator:
    def __init__(self, scores) -> None:
        self.scores = list(scores)
        self.calls = 0

    def evaluate(self, features, design_spec, html, browser_result, content_findings, ux_findings=None, visual_findings=None, model=None, think=None, timeout=None):
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        # readability is a local dimension, so the worst of these is one a
        # refinement is allowed to aim at.
        return _evaluation(**{**{name: score for name in DIMENSIONS}, "readability": max(0.0, score - 0.5)})


class _Refiner:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.received: list[str] = []
        self.specs: list[object] = []
        self.models: list[str | None] = []
        self.fail = fail

    def refine(self, features, html, evaluation, design_spec=None, content_findings=None, ux_findings=None, visual_findings=None, model=None, think=None, timeout=None, asset_root=None):
        self.calls += 1
        self.received.append(html)
        self.specs.append(design_spec)
        self.models.append(model)
        if self.fail:
            raise RefinerError("no")
        return PAGE.replace("본문", f"수정 {self.calls}")


def _run(engine_refiner, monkeypatch, tmp_path, scores, **refine):
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features, content_freedom="low":SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features, content_freedom="low": [])
    config = _config(**refine)
    pipeline = config.pipeline.model_copy(update={
        "max_iterations": 3, "evaluation_threshold": 99.0,
        "termination": config.pipeline.termination.model_copy(update={"enabled": True, "patience": 0}),
        "output": config.pipeline.output.model_copy(update={"save_mode": "best"}),
    })
    config = config.model_copy(update={"pipeline": pipeline})
    designer, generator = _Designer(), _Generator()
    engine = IterationEngine(config=config, designer=designer, generator=generator, evaluator=_Evaluator(scores), refiner=engine_refiner)
    page = PreparedPage(slug="index", relative="index.html", html="", features=FeatureModel(),
                        run_root=tmp_path, data_dir=tmp_path / "data" / "index", is_entry=True)
    results = engine.run_pages([page])
    return designer, generator, results


def test_later_iterations_refine_the_best_page_instead_of_regenerating(tmp_path: Path, monkeypatch) -> None:
    refiner = _Refiner()
    designer, generator, _ = _run(refiner, monkeypatch, tmp_path, [7.0, 8.0, 9.0], redesign_after_stalls=0)

    assert generator.calls == 1, "only the first iteration builds the page"
    assert designer.revisions == 0, "no redesign once there is a page to improve"
    assert refiner.calls == 2
    assert "생성 1" in refiner.received[0], "the refiner starts from the generated page"


def test_a_failing_refinement_falls_back_to_a_full_redesign(tmp_path: Path, monkeypatch) -> None:
    """An invalid edit must not cost the iteration."""
    refiner = _Refiner(fail=True)
    designer, generator, _ = _run(refiner, monkeypatch, tmp_path, [7.0, 8.0, 9.0], redesign_after_stalls=0)

    assert refiner.calls == 2
    assert designer.revisions == 2, "each failed refinement became a redesign"
    assert generator.calls == 3


def test_refining_stops_after_it_stops_helping(tmp_path: Path, monkeypatch) -> None:
    """Scores 8.0 then 7.0 then 7.0: the second refinement gives way to a redesign."""
    refiner = _Refiner()
    designer, generator, _ = _run(refiner, monkeypatch, tmp_path, [8.0, 7.0, 7.0], redesign_after_stalls=1)

    assert refiner.calls == 1, "the first refinement did not improve, so the next iteration redesigns"
    assert designer.revisions == 1


# --- the DesignSpec reaches the refiner -------------------------------------

def test_the_prompt_carries_the_design_spec() -> None:
    """Told not to change the palette, the refiner needs to see what it is."""
    client = ReplyClient(BETTER)
    _refiner(client).refine(FeatureModel(), PAGE, _evaluation(), design_spec=SPEC)

    assert "Current DesignSpec" in client.prompts[0]
    assert '"primary": "#111"' in client.prompts[0]


def test_a_missing_design_spec_says_so() -> None:
    client = ReplyClient(BETTER)
    _refiner(client).refine(FeatureModel(), PAGE, _evaluation())
    assert "Current DesignSpec:\nnot available" in client.prompts[0]


def test_the_engine_hands_the_refiner_the_best_page_design_spec(tmp_path: Path, monkeypatch) -> None:
    refiner = _Refiner()
    _run(refiner, monkeypatch, tmp_path, [7.0, 8.0, 9.0], redesign_after_stalls=0)

    assert refiner.calls == 2
    assert all(spec is not None for spec in refiner.specs), "the spec the page was built from"


# --- a structural weakness is not a refinement's job ------------------------

def test_structural_dimensions_are_never_targeted() -> None:
    """originality is the lowest here, but no local edit can raise it."""
    weakest = _refiner(ReplyClient(BETTER), weakest=3).weakest_dimensions(
        _evaluation(originality=2.0, visual_hierarchy=3.0, responsive_design=3.5, ux=6.0, readability=7.0))

    assert [name for name, _, _ in weakest] == ["ux", "readability", "design_consistency"]


def test_a_structurally_weak_page_is_redesigned_instead_of_refined() -> None:
    engine = _engine(_config())
    state = _state()
    state.best.evaluation = _evaluation(originality=4.0)

    assert not engine._refines(state), "the composition is the problem, not the markup"


def test_a_locally_weak_page_is_still_refined() -> None:
    engine = _engine(_config())
    state = _state()
    state.best.evaluation = _evaluation(html_quality=4.0)

    assert engine._refines(state)


def test_refining_uses_the_refiner_pool_not_the_generator_pool(tmp_path: Path, monkeypatch) -> None:
    """A refinement edits finished source text; the small generator models lose it."""
    refiner = _Refiner()
    _run(refiner, monkeypatch, tmp_path, [7.0, 8.0, 9.0], redesign_after_stalls=0)

    assert refiner.models, "the refiner ran"
    assert set(refiner.models) == {"gemma4:26b"}, "models.refiner, not models.generator"
