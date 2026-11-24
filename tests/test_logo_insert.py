from pathlib import Path
from types import SimpleNamespace

from test_hill_climbing import _config, _evaluation, _HtmlGenerator
from test_iteration_brief import DIRECTIVES, _RecordingEvaluator, _SpecDesigner, _page
from wra.core.iteration import IterationEngine
from wra.generator.logo_insert import insert_missing_logo
from wra.validator.adherence import DIRECTIVE_PREFIX

LOGO = "assets/logo.svg"


def test_the_logo_goes_at_the_start_of_the_first_header() -> None:
    html = "<html><body><header><h1>Site</h1></header><main>x</main></body></html>"

    result, repairs = insert_missing_logo(html, LOGO, "Acme")

    assert result.startswith(f'<html><body><header><img src="{LOGO}" alt="Acme" class="site-logo"')
    assert "height:40px" in result
    assert result.endswith("<h1>Site</h1></header><main>x</main></body></html>")
    assert len(repairs) == 1


def test_a_nav_is_used_when_there_is_no_header() -> None:
    html = '<html><body><nav class="top"><a href="#a">A</a></nav><main>x</main></body></html>'

    result, repairs = insert_missing_logo(html, LOGO)

    assert f'<nav class="top"><img src="{LOGO}" alt="logo"' in result
    assert repairs


def test_a_header_is_created_when_the_page_has_neither() -> None:
    html = '<html><body class="x"><main>x</main></body></html>'

    result, repairs = insert_missing_logo(html, LOGO, 'A "B" & C')

    assert result.startswith(f'<html><body class="x"><header><img src="{LOGO}" alt="A &quot;B&quot; &amp; C"')
    assert "</header><main>x</main>" in result
    assert repairs


def test_a_page_that_already_references_the_logo_is_unchanged() -> None:
    for html in (f'<body><header><img src="{LOGO}"></header></body>', f"<body><style>.l{{background:url({LOGO})}}</style></body>"):
        assert insert_missing_logo(html, LOGO) == (html, [])


def test_the_insert_is_idempotent() -> None:
    html = "<html><body><header>h</header></body></html>"

    once, _ = insert_missing_logo(html, LOGO)

    assert insert_missing_logo(once, LOGO) == (once, [])


def test_markup_inside_comments_and_scripts_is_not_a_target() -> None:
    html = "<html><body><!-- <header>old</header> --><script>var h='<header>';</script><main>x</main></body></html>"

    result, _ = insert_missing_logo(html, LOGO)

    assert result.startswith("<html><body><header><img")
    assert result.count("<img") == 1


def test_a_document_without_a_body_is_left_alone() -> None:
    assert insert_missing_logo("<p>fragment</p>", LOGO) == ("<p>fragment</p>", [])


def test_the_engine_inserts_the_brief_logo_into_the_generated_page(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("wra.core.iteration.validate_html", lambda html, features, content_freedom="low": SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr("wra.core.iteration.find_corrupted_tokens", lambda html, features, content_freedom="low": [])
    config = _config(patience=0)
    config = config.model_copy(update={"pipeline": config.pipeline.model_copy(update={"max_iterations": 1, "evaluation_threshold": 99.0, "refine": config.pipeline.refine.model_copy(update={"enabled": False}), "output": config.pipeline.output.model_copy(update={"save_mode": "best"})})})
    evaluator = _RecordingEvaluator([_evaluation(7.0)])
    generator = _HtmlGenerator(["<html><body><header><h1>Site</h1></header><main><p>x</p></main></body></html>"])
    engine = IterationEngine(config=config, designer=_SpecDesigner(), generator=generator, evaluator=evaluator)

    results = engine.run_pages([_page(tmp_path, directives=DIRECTIVES)])

    assert f'<header><img src="{LOGO}"' in results[0].html
    assert not any(item.startswith(DIRECTIVE_PREFIX) and "logo" in item for item in evaluator.visual[0])
