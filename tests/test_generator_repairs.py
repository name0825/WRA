from wra.core.models import FeatureModel, FrameworkConfig
from wra.generator.generator import HTMLGenerator
from wra.generator.main_repair import repair_missing_main
from wra.generator.theme_tokens import root_block
from wra.validator.structure import validate_structure

from tests.test_generator import make_spec

_PAGE = "<!DOCTYPE html><html><head><title>t</title></head><body>{body}</body></html>"


def test_root_block_lists_tokens_from_spec():
    block = root_block(make_spec())

    assert block == ":root{--color-bg:#fff;--color-surface:#f5f5f5;--color-text:#111;--color-muted:#666;--color-primary:#000;--color-accent:#333;--font-heading:sans-serif;--font-body:sans-serif;}"


def test_root_block_skips_empty_values():
    spec = make_spec()
    spec.design.color_system.accent = "  "

    assert "--color-accent" not in root_block(spec)


def test_prompt_contains_root_tokens_block():
    prompt = HTMLGenerator._build_prompt("TEMPLATE", FeatureModel(), make_spec(), None, "", FrameworkConfig())

    assert "Required :root tokens" in prompt
    assert ":root{--color-bg:#fff;" in prompt
    assert prompt.index(":root{--color-bg") < prompt.index("TEMPLATE")


def test_main_inserted_between_header_and_footer():
    html = _PAGE.format(body="<header><h1>A</h1></header><section><p>x</p></section><article>y</article><footer>f</footer>")

    result, repairs = repair_missing_main(html)

    assert "<header><h1>A</h1></header><main><section><p>x</p></section><article>y</article></main><footer>f</footer>" in result
    assert repairs
    errors, warnings = validate_structure(result)
    assert not errors and not warnings


def test_main_wraps_to_body_end_without_footer():
    html = _PAGE.format(body="<nav>n</nav><section>x</section>")

    result, _ = repair_missing_main(html)

    assert "<nav>n</nav><main><section>x</section></main></body>" in result


def test_existing_main_left_unchanged():
    html = _PAGE.format(body="<main><section>x</section></main>")

    assert repair_missing_main(html) == (html, [])


def test_main_not_inserted_when_sections_are_nested():
    html = _PAGE.format(body='<div class="wrap"><section>x</section></div><footer>f</footer>')

    assert repair_missing_main(html) == (html, [])


def test_main_not_inserted_when_chrome_follows_content():
    html = _PAGE.format(body="<section>x</section><nav>n</nav><section>y</section>")

    assert repair_missing_main(html) == (html, [])


def test_main_ignores_tags_inside_script_and_comments():
    html = _PAGE.format(body="<!-- <main> --><script>var s = '<section>';</script><section>x</section>")

    result, _ = repair_missing_main(html)

    assert "<main><section>x</section></main>" in result


def test_main_excludes_footer_and_script_with_unclosed_implied_end_tags():
    html = _PAGE.format(body="<header>H</header><section><p>a<p>b</section><footer>F</footer><script>x</script>")

    result, _ = repair_missing_main(html)

    assert "<main><section><p>a<p>b</section></main><footer>F</footer><script>x</script></body>" in result


def test_main_unchanged_when_footer_not_found_at_top_level():
    html = _PAGE.format(body="<section><div>a</section><footer>F</footer>")

    assert repair_missing_main(html) == (html, [])
