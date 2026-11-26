from pathlib import Path

from test_generator import FakeLLMClient, make_spec

from wra.core.models import FeatureModel, Framework, FrameworkConfig
from wra.generator.generator import HTMLGenerator
from wra.generator.theme_tokens import DEFAULT_TOKENS, ensure_theme_skeleton, resolved_tokens

BARE = '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>t</title><style>h1{margin:0}</style></head><body><main><h1>x</h1></main></body></html>'
BOOTSTRAP = Framework(name="Bootstrap", cdn="https://cdn.example/bootstrap.min.css", tag="link")


def _spec(**colors):
    spec = make_spec("Bootstrap")
    for field, value in colors.items():
        setattr(spec.design.color_system, field, value)
    return spec


def test_an_unusable_designer_value_falls_back_to_the_preset_and_a_usable_one_wins() -> None:
    spec = _spec(background="light airy cream", primary="#112233")
    spec.design.typography.heading = "A bold geometric sans for headlines and more"
    spec.design.typography.body = "Pretendard"

    tokens = resolved_tokens(spec)

    assert tokens["--color-bg"] == DEFAULT_TOKENS["--color-bg"]
    assert tokens["--color-primary"] == "#112233"
    assert tokens["--font-heading"] == DEFAULT_TOKENS["--font-heading"]
    assert tokens["--font-body"] == "Pretendard, sans-serif"


def test_the_skeleton_carries_tokens_and_a_base_layer_ahead_of_the_authors_style() -> None:
    page, repairs = ensure_theme_skeleton(BARE, make_spec())

    assert repairs
    assert '<style id="wra-theme" data-wra-repair="skeleton">' in page
    assert "--color-bg:#fff" in page and "body{background:var(--color-bg)" in page and "a{color:var(--color-primary)}" in page
    assert "--bs-body-bg" not in page
    assert page.index("wra-theme") < page.index("h1{margin:0}")


def test_bootstrap_pages_bridge_the_framework_variables_to_the_tokens() -> None:
    page, _ = ensure_theme_skeleton(BARE, _spec(background="#102030"))

    assert "--bs-body-bg:var(--color-bg)" in page
    assert "--bs-link-color:var(--color-primary)" in page
    assert "--color-bg:#102030" in page


def test_the_skeleton_is_idempotent_and_refreshes_a_stale_block() -> None:
    once, _ = ensure_theme_skeleton(BARE, make_spec())
    again, repairs = ensure_theme_skeleton(once, make_spec())
    assert again == once and repairs == []

    stale = once.replace("--color-bg:#fff", "--color-bg:#abcdef")
    refreshed, repairs = ensure_theme_skeleton(stale, make_spec())
    assert refreshed == once and repairs
    assert once.count("wra-theme") == 1


def test_it_sits_after_a_cdn_framework_sheet_and_before_local_and_inline_styles() -> None:
    html = '<html><head><link rel="stylesheet" href="https://cdn.example/bootstrap.min.css"><link rel="stylesheet" href="style.css"><style>p{}</style></head><body></body></html>'

    page, _ = ensure_theme_skeleton(html, make_spec())

    assert page.index("bootstrap.min.css") < page.index("wra-theme") < page.index('href="style.css"') < page.index("p{}")


def test_a_head_without_author_css_gets_it_before_the_closing_tag_and_headless_markup_is_left_alone() -> None:
    page, _ = ensure_theme_skeleton("<html><head><title>t</title></head><body></body></html>", make_spec())
    assert page.index("wra-theme") < page.index("</head>")

    assert ensure_theme_skeleton("<p>fragment</p>", make_spec()) == ("<p>fragment</p>", [])


def test_a_generated_bare_bootstrap_page_comes_out_themed(tmp_path: Path) -> None:
    prompt = tmp_path / "system.md"
    prompt.write_text("Generate HTML.", encoding="utf-8")
    bare = '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>t</title></head><body><main><h1>x</h1></main></body></html>'
    generator = HTMLGenerator(FakeLLMClient(bare), "m", FrameworkConfig(css=[BOOTSTRAP]), prompt)

    html = generator.generate(FeatureModel(), _spec(background="#102030"))

    assert html.count('id="wra-theme"') == 1
    assert "--bs-body-bg:var(--color-bg)" in html
    assert html.index("bootstrap.min.css") < html.index("wra-theme")
