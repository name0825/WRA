import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wra.cli import main
from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
from wra.core.inputs import discover
from wra.core.iteration import IterationEngine
from wra.core.models import EvaluationResult, FeatureModel, ModelSpec, PageImpression, VisionInsight, WRAConfig
from wra.core.preparation import InputPreparer
from wra.designer.schema import ColorSystem, Design, DesignSpec, FrameworkRecommendation, FrameworkRecommendations, InteractionSpec, Motion, ResponsiveSpec, Typography
from support import sample_config_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StubAnalyzer:
    """Deterministic stand-in for the LLM-backed analyzer."""

    def analyze(self, html: str) -> FeatureModel:
        features = FeatureModel()
        features.metadata.title = "Stub"
        return features


def _config(**input_overrides) -> WRAConfig:
    data = sample_config_data()
    data["input"] = {**data.get("input", {}), **input_overrides}
    data["input"]["vision"] = {**data["input"].get("vision", {}), "enabled": False}
    return WRAConfig.model_validate(data)


def _site(root: Path) -> Path:
    (root / "img").mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><head><title>Home</title></head><body><h1>Home</h1>'
        '<img src="/img/hero.png" alt="Hero"><a href="about.html">About</a></body></html>',
        encoding="utf-8",
    )
    (root / "about.html").write_text(
        "<html><head><title>About</title></head><body><h1>About</h1></body></html>",
        encoding="utf-8",
    )
    (root / "img" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8192)
    return root


def _prepare(tmp_path: Path, mode: str):
    root = _site(tmp_path / "input")
    run_dir = tmp_path / "run"
    config = _config(mode=mode)
    bundle = discover(root, entry=config.input.entry, asset_directory=config.input.asset_directory)
    pages = InputPreparer(config=config, analyzer=StubAnalyzer()).prepare(bundle, run_dir)
    return pages, run_dir


def test_entry_mode_prepares_only_the_entry_page(tmp_path: Path) -> None:
    pages, run_dir = _prepare(tmp_path, "entry")
    assert [page.slug for page in pages] == ["index"]
    assert pages[0].data_dir == run_dir / "data" / "index"


@pytest.mark.parametrize("mode", ["pages", "site"])
def test_multi_page_modes_prepare_every_page(tmp_path: Path, mode: str) -> None:
    pages, run_dir = _prepare(tmp_path, mode)
    assert [page.slug for page in pages] == ["index", "about"]
    assert pages[0].data_dir == run_dir / "data" / "index"
    assert pages[1].data_dir == run_dir / "data" / "about"
    assert pages[1].site_path == run_dir / "about.html"


def test_preparation_copies_assets_and_rewrites_references(tmp_path: Path) -> None:
    pages, run_dir = _prepare(tmp_path, "entry")
    assert (run_dir / "assets" / "hero.png").is_file()
    assert 'src="assets/hero.png"' in pages[0].html
    assert 'src="assets/hero.png"' in (run_dir / "data" / "index" / "original.html").read_text(encoding="utf-8")


def test_multi_page_mode_rewrites_cross_page_links(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "site")
    assert 'href="about.html"' in pages[0].html


def test_prepared_features_carry_the_source_asset_digest(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "entry")
    assets = pages[0].features.source_assets
    assert [asset.path for asset in assets.assets] == ["assets/hero.png"]
    assert assets.impression is None


class StubVision:
    """Stands in for the vision analyzer without touching a model or a browser."""

    def __init__(self) -> None:
        self.pages: list[Path] = []

    def analyze_assets(self, assets, source_paths) -> None:
        for asset in assets:
            asset.vision = VisionInsight(subject="stub", role="photo")

    def analyze_page(self, screenshot_path):
        self.pages.append(Path(screenshot_path))
        return PageImpression(mood=["stub"], density="stub", layout="stub")


def test_every_page_keeps_its_own_source_screenshot(tmp_path: Path, monkeypatch) -> None:
    """Page two's capture must not overwrite page one's."""
    captured: list[Path] = []

    def fake_render(html, asset_root, screenshot_path, browser_config, reveal_animations=True):
        path = Path(screenshot_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Distinct bytes per page, so an overwrite would be visible.
        path.write_bytes(path.parent.name.encode("utf-8"))
        captured.append(path)
        return path

    monkeypatch.setattr("wra.core.preparation.render_source_page", fake_render)
    root = _site(tmp_path / "input")
    run_dir = tmp_path / "run"
    config = _config(mode="site")
    config = config.model_copy(update={"input": config.input.model_copy(update={"vision": config.input.vision.model_copy(update={"enabled": True})})})
    bundle = discover(root, asset_directory=config.input.asset_directory)
    vision = StubVision()
    pages = InputPreparer(config=config, analyzer=StubAnalyzer(), vision_analyzer=vision).prepare(bundle, run_dir)

    assert len(captured) == 2
    assert len({path for path in captured}) == 2
    assert vision.pages == captured
    assert (run_dir / "data" / "index" / "source.png").read_bytes() == b"index"
    assert (run_dir / "data" / "about" / "source.png").read_bytes() == b"about"
    assert all(page.features.source_assets.impression is not None for page in pages)


def test_vision_insights_reach_the_asset_inventory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wra.core.preparation.render_source_page", lambda *args, **kwargs: None)
    root = _site(tmp_path / "input")
    config = _config(mode="entry")
    bundle = discover(root, asset_directory=config.input.asset_directory)
    pages = InputPreparer(config=config, analyzer=StubAnalyzer(), vision_analyzer=StubVision()).prepare(bundle, tmp_path / "run")
    assert pages[0].features.source_assets.assets[0].vision.role == "photo"


class RecordingEngine(IterationEngine):
    """Drives the real loop with the per-iteration work stubbed out."""

    def __init__(self, config, recorded):
        super().__init__(config=config, designer=None, generator=None, evaluator=None)
        self.recorded = recorded
        self.systems: list = []
        self.models: list = []

    def _browser_result(self, state, iteration, browser_tmp):
        return None

    def _run_one_iteration(self, state, iteration, site_context, output_manager, browser_tmp, models=None, design_system=None):
        self.recorded.append((iteration, state.page.slug, site_context))
        self.systems.append(design_system)
        self.models.append(models["designer"].name if models else None)
        html = f"<html><body><h1>{state.page.slug} v{iteration}</h1></body></html>"
        output_manager.save_iteration(iteration=iteration, slug=state.page.slug, html=html, browser=self._browser_result(state, iteration, browser_tmp), score=float(iteration))
        return SimpleNamespace(
            slug=state.page.slug,
            iteration=iteration,
            html=html,
            evaluation=None,
            features=state.page.features,
            design_spec=None,
            validation=None,
            browser=None,
            html_path=None,
            validation_path=None,
        )


def _capped(config, max_iterations: int) -> WRAConfig:
    pipeline = config.pipeline.model_copy(update={
        "max_iterations": max_iterations,
        "termination": config.pipeline.termination.model_copy(update={"enabled": True}),
    })
    return config.model_copy(update={"pipeline": pipeline})


def test_every_page_advances_one_iteration_before_any_page_advances_twice(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "pages")
    recorded: list = []
    RecordingEngine(_capped(_config(mode="pages"), 3), recorded).run_pages(pages)

    order = [(iteration, slug) for iteration, slug, _ in recorded]
    assert order == [
        (1, "index"), (1, "about"),
        (2, "index"), (2, "about"),
        (3, "index"), (3, "about"),
    ]


def test_a_page_that_finishes_early_stops_being_iterated(tmp_path: Path) -> None:
    """Termination is per page: a finished page drops out, the rest carry on."""
    pages, _ = _prepare(tmp_path, "pages")
    recorded: list = []
    engine = RecordingEngine(_capped(_config(mode="pages"), 3), recorded)
    original = engine._should_terminate
    engine._should_terminate = lambda iteration, evaluation: original(iteration, evaluation) or (iteration == 1 and recorded[-1][1] == "about")
    engine.run_pages(pages)

    assert [(i, slug) for i, slug, _ in recorded] == [
        (1, "index"), (1, "about"),
        (2, "index"),
        (3, "index"),
    ]


def test_multi_page_run_publishes_pages_beside_the_shared_assets(tmp_path: Path) -> None:
    pages, run_dir = _prepare(tmp_path, "site")
    RecordingEngine(_capped(_config(mode="site"), 1), []).run_pages(pages)

    assert (run_dir / "index.html").is_file()
    assert (run_dir / "about.html").is_file()
    assert (run_dir / "assets" / "hero.png").is_file()
    # The best iteration, not merely the last one.
    assert "index v1" in (run_dir / "index.html").read_text(encoding="utf-8")
    assert (run_dir / "iterations" / "iter1" / "index.html").is_file()
    assert (run_dir / "iterations" / "iter1" / "about.html").is_file()


def _scored(score: float) -> EvaluationResult:
    payload = {
        name: {"score": score, "feedback": ""}
        for name in ("design_completeness", "visual_hierarchy", "design_consistency", "content_preservation",
                     "ux", "readability", "responsive_design", "originality", "html_quality")
    }
    return EvaluationResult.model_validate({**payload, "overall_score": score, "summary": ""})


class SpecRecordingEngine(RecordingEngine):
    """Returns a real DesignSpec, so the design-system hand-off can be observed."""

    # Each iteration scores higher than the last, so every attempt is accepted
    # and the entry page's best design is also its most recent one.
    def _score(self, iteration: int) -> float:
        return float(iteration)

    def _design_spec(self, slug: str, iteration: int) -> DesignSpec:
        return DesignSpec(
            design=Design(
                concept=f"{slug} {iteration}", visual_direction="d", layout="l",
                color_system=ColorSystem(background="#fff", surface="#eee", text="#111", muted_text="#666", primary=f"#00000{iteration}", accent="#f00"),
                typography=Typography(heading=f"Lead {iteration}", body="Body", scale="1.2", weight="400"),
                framework=FrameworkRecommendations(css=FrameworkRecommendation(name="Bootstrap", reason="r"), js=FrameworkRecommendation(name="GSAP", reason="r")),
                motion=Motion(level="low", principles=["subtle"]),
            ),
            sections=[],
            responsive=ResponsiveSpec(strategy="mobile-first", breakpoints=[], behavior=[]),
            interaction=InteractionSpec(),
        )

    def _run_one_iteration(self, state, iteration, site_context, output_manager, browser_tmp, models=None, design_system=None):
        result = super()._run_one_iteration(state, iteration, site_context, output_manager, browser_tmp, models, design_system)
        return SimpleNamespace(**{**result.__dict__, "design_spec": self._design_spec(state.page.slug, iteration), "evaluation": _scored(self._score(iteration))})


class BrowserRecordingEngine(RecordingEngine):
    """Produces browser artifacts in the temp directory, as the real run does."""

    def _browser_result(self, state, iteration, browser_tmp):
        shot_dir = browser_tmp / state.page.slug / f"iteration_{iteration:03d}"
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = shot_dir / "desktop.png"
        shot.write_bytes(b"png")
        return BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path=str(shot))])


def test_the_best_browser_artifacts_survive_the_run(tmp_path: Path) -> None:
    """They live in a temp directory that must outlast finalizing, not precede it."""
    pages, run_dir = _prepare(tmp_path, "pages")
    BrowserRecordingEngine(_capped(_config(mode="pages"), 1), []).run_pages(pages)

    assert (run_dir / "data" / "index" / "browser" / "desktop.png").is_file()
    assert (run_dir / "data" / "about" / "browser" / "desktop.png").is_file()
    assert not (run_dir / ".browser_tmp").exists()


def test_site_mode_shares_one_site_context(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "site")
    recorded: list = []
    RecordingEngine(_capped(_config(mode="site"), 1), recorded).run_pages(pages)

    contexts = [context for _, _, context in recorded]
    assert all(context is not None for context in contexts)
    assert contexts[0] is contexts[1]
    assert [entry["file"] for entry in contexts[0]["pages"]] == ["index.html", "about.html"]


def test_models_are_chosen_once_per_iteration_not_once_per_page(tmp_path: Path) -> None:
    """Two pages designed by different models in one iteration will not match."""
    pages, _ = _prepare(tmp_path, "pages")
    engine = RecordingEngine(_capped(_config(mode="pages"), 3), [])
    selections = []
    original = engine._select_models
    engine._select_models = lambda: selections.append(1) or original()
    engine.run_pages(pages)

    assert len(pages) == 2
    assert len(engine.recorded) == 6, "two pages across three iterations"
    assert len(selections) == 3, "one selection per iteration, shared by both pages"


def test_every_page_of_an_iteration_gets_the_same_models(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "pages")
    config = _capped(_config(mode="pages"), 3)
    designers = [ModelSpec(name=f"designer-{index}") for index in range(6)]
    config = config.model_copy(update={"models": config.models.model_copy(update={"designer": designers})})
    engine = RecordingEngine(config, [])
    engine.run_pages(pages)

    per_iteration: dict[int, set] = {}
    for (iteration, _, _), model in zip(engine.recorded, engine.models):
        per_iteration.setdefault(iteration, set()).add(model)
    assert all(len(chosen) == 1 for chosen in per_iteration.values())


def test_the_run_records_its_page_order_before_redesigning_anything(tmp_path: Path) -> None:
    """A reader opening the run mid-flight must still see which page is the entry."""
    pages, run_dir = _prepare(tmp_path, "pages")

    class StopsImmediately(RecordingEngine):
        def _run_one_iteration(self, *args, **kwargs):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        StopsImmediately(_capped(_config(mode="pages"), 3), []).run_pages(pages)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert list(result["pages"]) == ["index", "about"]
    assert result["entry"] == "index"


def test_sibling_pages_inherit_the_entry_page_design_system(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "site")
    engine = SpecRecordingEngine(_capped(_config(mode="site"), 2), [])
    engine.run_pages(pages)

    by_page = {(iteration, slug): system for (iteration, slug, _), system in zip(engine.recorded, engine.systems)}
    # The entry page leads, so it is never handed a system to follow.
    assert by_page[(1, "index")] is None
    assert by_page[(2, "index")] is None
    for iteration in (1, 2):
        system = by_page[(iteration, "about")]
        assert system is not None
        assert system["source_page"] == "index.html"
        assert system["color_system"]["primary"] == f"#00000{iteration}"
        assert system["typography"]["heading"] == f"Lead {iteration}"


def test_siblings_follow_the_entry_pages_best_design_not_its_latest(tmp_path: Path) -> None:
    """A rejected entry-page attempt must not become the palette the siblings copy."""
    pages, _ = _prepare(tmp_path, "site")

    class EntryRegresses(SpecRecordingEngine):
        def _score(self, iteration: int) -> float:
            return 6.0 if iteration == 1 else 4.0

    engine = EntryRegresses(_capped(_config(mode="site"), 2), [])
    engine.run_pages(pages)

    by_page = {(iteration, slug): system for (iteration, slug, _), system in zip(engine.recorded, engine.systems)}
    assert by_page[(2, "about")]["color_system"]["primary"] == "#000001"


def test_a_single_page_run_locks_nothing(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "entry")
    engine = SpecRecordingEngine(_capped(_config(mode="entry"), 2), [])
    engine.run_pages(pages)
    assert engine.systems == [None, None]


def test_pages_mode_gives_no_site_context(tmp_path: Path) -> None:
    pages, _ = _prepare(tmp_path, "pages")
    recorded: list = []
    RecordingEngine(_capped(_config(mode="pages"), 1), recorded).run_pages(pages)
    assert [context for _, _, context in recorded] == [None, None]


def test_cli_creates_feature_model(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "input.html").write_text(
        "<html><head><title>Test</title></head><body><h1>Hello</h1></body></html>",
        encoding="utf-8",
    )

    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setattr("wra.cli.INPUT_DIR", input_dir)
    monkeypatch.setattr("wra.cli.OUTPUT_ROOT", output_dir)
    config_data = sample_config_data()
    config_data.setdefault("viewer", {}).setdefault("admin", {})["password"] = "hunter2-secret"
    monkeypatch.setattr("wra.cli.load_config", lambda: WRAConfig.model_validate(config_data))
    monkeypatch.setattr("wra.cli.warn_if_vision_missing", lambda *args, **kwargs: None)

    class OfflineClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def generate(self, *args, **kwargs) -> str:
            raise ConnectionError("no LLM in tests")

    finished = SimpleNamespace(slug="input", final_path=Path("final.html"), html_path=None, validation_path=None, evaluation=None, validation=SimpleNamespace(valid=True, errors=[], warnings=[]), browser=SimpleNamespace(enabled=False))

    class StubEngine(IterationEngine):
        def run_pages(self, pages) -> list:
            for page in pages:
                self._start_page(page)
            return [finished]

    monkeypatch.setattr("wra.cli.OllamaClient", OfflineClient)
    monkeypatch.setattr("wra.cli.IterationEngine", StubEngine)

    main()

    config_files = list(output_dir.glob("run_*/config.json"))
    assert len(config_files) == 1
    snapshot_text = config_files[0].read_text(encoding="utf-8")
    snapshot = json.loads(snapshot_text)
    assert isinstance(snapshot["resolved_seed"], int)
    assert snapshot["content"]["freedom"] == "low"
    assert "password" not in snapshot["viewer"]["admin"]
    assert "hunter2-secret" not in snapshot_text

    # Since the run directory was reorganized, a page's Feature Model lives at
    # run_*/data/<slug>/features.json rather than at the run root.
    feature_files = list(output_dir.glob("run_*/data/*/features.json"))
    assert len(feature_files) == 1
    assert '"title": "Test"' in feature_files[0].read_text(encoding="utf-8")
    assert '"text": "Hello"' in feature_files[0].read_text(encoding="utf-8")
