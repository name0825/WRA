"""The search discipline around the iteration loop: never build on a worse page."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from wra.core.iteration import IterationEngine, _PageRun
from wra.core.models import EvaluationDimension, EvaluationResult, FeatureModel, WRAConfig
from wra.core.preparation import PreparedPage
from support import sample_config_data


def _evaluation(score: float, **dimensions) -> EvaluationResult:
    payload = {
        name: {"score": dimensions.get(name, score), "feedback": ""}
        for name in ("design_completeness", "visual_hierarchy", "design_consistency", "content_preservation",
                     "ux", "readability", "responsive_design", "originality", "html_quality")
    }
    payload["overall_score"] = 0.0
    payload["summary"] = f"scored {score}"
    return EvaluationResult.model_validate(payload)


def _result(iteration: int, evaluation: EvaluationResult | None) -> SimpleNamespace:
    return SimpleNamespace(iteration=iteration, evaluation=evaluation, slug="index", html=f"<html>{iteration}</html>")


def _state() -> _PageRun:
    page = PreparedPage(slug="index", relative="index.html", html="", features=FeatureModel(),
                        run_root=Path("."), data_dir=Path("."), is_entry=True)
    return _PageRun(page=page)


def test_the_first_result_becomes_the_base() -> None:
    state = _state()
    assert state.record(_result(1, _evaluation(7.0)))
    assert state.best.iteration == 1
    assert state.stalled == 0


def test_a_better_result_replaces_the_base() -> None:
    state = _state()
    state.record(_result(1, _evaluation(7.0)))
    assert state.record(_result(2, _evaluation(8.5)))
    assert state.best.iteration == 2
    assert state.best_score == pytest.approx(8.5)


def test_a_worse_result_is_recorded_but_not_used_as_the_base() -> None:
    """The failure that let one bad iteration poison every iteration after it."""
    state = _state()
    state.record(_result(1, _evaluation(8.5)))
    assert not state.record(_result(2, _evaluation(4.0)))

    assert state.best.iteration == 1, "the next revision must start from the better page"
    assert state.last.iteration == 2, "the attempt is still recorded"
    assert state.stalled == 1


def test_an_unscorable_result_never_becomes_the_base() -> None:
    state = _state()
    state.record(_result(1, _evaluation(8.0)))
    assert not state.record(_result(2, None))
    assert state.best.iteration == 1


def test_a_first_result_that_cannot_be_scored_is_still_something_to_build_on() -> None:
    state = _state()
    assert state.record(_result(1, None))
    assert state.best.iteration == 1


def test_the_rejected_attempt_is_described_for_the_next_one() -> None:
    state = _state()
    state.record(_result(1, _evaluation(9.0)))
    state.record(_result(2, _evaluation(9.0, ux=4.0, readability=5.0)))

    setback = state.setback
    assert setback is not None
    assert setback["iteration"] == 2
    assert setback["best_score"] == pytest.approx(9.0)
    assert "ux: 9 -> 4" in setback["regressed"]
    assert "readability: 9 -> 5" in setback["regressed"]
    assert "design_completeness" not in " ".join(setback["regressed"])


def test_an_improvement_clears_the_previous_setback() -> None:
    state = _state()
    state.record(_result(1, _evaluation(8.0)))
    state.record(_result(2, _evaluation(6.0)))
    assert state.setback is not None
    state.record(_result(3, _evaluation(9.0)))
    assert state.setback is None
    assert state.stalled == 0


# --- termination ------------------------------------------------------------

def _config(**termination) -> WRAConfig:
    data = sample_config_data()
    data["pipeline"] = {**data["pipeline"], "max_iterations": 99, "evaluation_threshold": 9.5,
                        "termination": {"enabled": True, **termination}}
    return WRAConfig.model_validate(data)


def _engine(config: WRAConfig) -> IterationEngine:
    return IterationEngine(config=config, designer=None, generator=None, evaluator=None)


def test_patience_stops_a_page_that_stopped_improving() -> None:
    engine = _engine(_config(patience=2))
    state = _state()
    state.record(_result(1, _evaluation(8.0)))
    assert not engine._should_terminate(1, state)
    state.record(_result(2, _evaluation(7.0)))
    assert not engine._should_terminate(2, state)
    state.record(_result(3, _evaluation(7.5)))
    assert engine._should_terminate(3, state), "two iterations without beating 8.0"


def test_patience_is_reset_by_any_improvement() -> None:
    engine = _engine(_config(patience=2))
    state = _state()
    state.record(_result(1, _evaluation(8.0)))
    state.record(_result(2, _evaluation(7.0)))
    state.record(_result(3, _evaluation(8.5)))
    assert not engine._should_terminate(3, state)


def test_patience_zero_leaves_the_run_to_max_iterations() -> None:
    engine = _engine(_config(patience=0))
    state = _state()
    state.record(_result(1, _evaluation(8.0)))
    for iteration in range(2, 6):
        state.record(_result(iteration, _evaluation(7.0)))
        assert not engine._should_terminate(iteration, state)


def test_the_threshold_is_judged_on_the_best_not_the_latest() -> None:
    """A page that already reached the bar is finished, even if one attempt fell."""
    engine = _engine(_config(patience=0))
    state = _state()
    state.record(_result(1, _evaluation(9.6)))
    state.record(_result(2, _evaluation(6.0)))
    assert engine._should_terminate(2, state)


def test_a_disabled_termination_ignores_patience() -> None:
    data = sample_config_data()
    data["pipeline"] = {**data["pipeline"], "termination": {"enabled": False, "patience": 1}}
    engine = _engine(WRAConfig.model_validate(data))
    state = _state()
    state.record(_result(1, _evaluation(8.0)))
    state.record(_result(2, _evaluation(5.0)))
    assert not engine._should_terminate(2, state)


# --- the engine actually hands the reviser the better page -------------------

class _Designer:
    """Records which page each revision was asked to improve."""

    def __init__(self) -> None:
        self.revised_from: list[str] = []

    def plan(self, features, design_constraints, model, think=None, timeout=None):
        return SimpleNamespace(sections=[], design=None, responsive=None, interaction=None)

    def revise(self, features, previous_design_spec, previous_html, evaluation, design_constraints, model, think=None, timeout=None):
        self.revised_from.append(previous_html)
        return SimpleNamespace(sections=[], design=None, responsive=None, interaction=None)


class _Generator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, features, design_spec, generation_rules, model, think=None, timeout=None):
        self.calls += 1
        return f"<html>v{self.calls}</html>"


class _Evaluator:
    """Scores 8.5, then 4.0, then 5.0: one good page followed by two bad ones."""

    def __init__(self, scores) -> None:
        self.scores = list(scores)
        self.calls = 0

    def evaluate(self, features, design_spec, html, browser_result, content_findings, model, think=None, timeout=None):
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return _evaluation(score)


class _RaisingEvaluator:
    """Fails every evaluation, the way an over-context Ollama call does."""

    def evaluate(self, features, design_spec, html, browser_result, content_findings, model, think=None, timeout=None):
        raise RuntimeError("evaluator exploded")


def test_a_failed_evaluation_still_saves_the_iteration_then_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features: SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features: [])

    designer, generator = _Designer(), _Generator()
    config = _config(patience=0)
    pipeline = config.pipeline.model_copy(update={"output": config.pipeline.output.model_copy(update={"save_mode": "best"})})
    config = config.model_copy(update={"pipeline": pipeline})
    engine = IterationEngine(config=config, designer=designer, generator=generator, evaluator=_RaisingEvaluator())

    page = PreparedPage(slug="index", relative="index.html", html="", features=FeatureModel(),
                        run_root=tmp_path, data_dir=tmp_path / "data" / "index", is_entry=True)
    state = _PageRun(page=page)

    from wra.core.output import OutputManager
    output_manager = OutputManager(tmp_path, config.pipeline.output.save_mode)
    save_calls = []
    original_save = output_manager.save_iteration

    def spy_save(**kwargs):
        save_calls.append(kwargs)
        return original_save(**kwargs)

    monkeypatch.setattr(output_manager, "save_iteration", spy_save)

    with pytest.raises(RuntimeError, match="evaluator exploded"):
        engine._run_one_iteration(state, 1, None, output_manager, tmp_path / ".browser_tmp")

    assert len(save_calls) == 1
    assert save_calls[0]["evaluation"] is None
    assert save_calls[0]["score"] is None
    assert save_calls[0]["html"] == "<html>v1</html>"


def test_the_engine_revises_the_best_page_not_the_last(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features: SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features: [])

    designer, generator = _Designer(), _Generator()
    config = _config(patience=0)
    # save_mode "best" keeps the run from serialising the stub DesignSpec.
    pipeline = config.pipeline.model_copy(update={"max_iterations": 3, "output": config.pipeline.output.model_copy(update={"save_mode": "best"})})
    config = config.model_copy(update={"pipeline": pipeline})
    engine = IterationEngine(config=config, designer=designer, generator=generator, evaluator=_Evaluator([8.5, 4.0, 5.0]))

    page = PreparedPage(slug="index", relative="index.html", html="", features=FeatureModel(),
                        run_root=tmp_path, data_dir=tmp_path / "data" / "index", is_entry=True)
    results = engine.run_pages([page])

    # v1 scored 8.5, v2 scored 4.0. Both later revisions must start from v1.
    assert designer.revised_from == ["<html>v1</html>", "<html>v1</html>"]
    assert results[0].html == "<html>v1</html>", "the best page is what gets finalized"


def _spec(primary: str) -> "DesignSpec":
    from test_generator import make_spec

    spec = make_spec()
    spec.design.color_system.primary = primary
    return spec


def test_siblings_follow_the_entry_pages_best_design_not_its_latest() -> None:
    """A rejected entry-page attempt must not set the palette the site is built on."""
    lead, sibling = _state(), _state()
    lead.record(SimpleNamespace(**{**vars(_result(1, _evaluation(8.5))), "design_spec": _spec("#good")}))
    lead.record(SimpleNamespace(**{**vars(_result(2, _evaluation(4.0))), "design_spec": _spec("#bad")}))
    assert lead.best.design_spec.design.color_system.primary == "#good"

    system = IterationEngine._design_system([lead, sibling], sibling)

    assert system is not None
    assert system["color_system"]["primary"] == "#good"


def test_no_design_system_before_the_entry_page_has_run() -> None:
    lead, sibling = _state(), _state()
    assert IterationEngine._design_system([lead, sibling], sibling) is None
