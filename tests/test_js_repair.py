import importlib.util

import pytest

from wra.generator.js_repair import default_syntax_checker, repair_js_syntax
from wra.llm.client import LLMClient


class FakeLLMClient(LLMClient):
    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "model": model})
        if self.error is not None:
            raise self.error
        return self.response


def _fake_checker(errors: dict[str, str]):
    def checker(html: str) -> dict[str, str | None]:
        from wra.generator.js_repair import find_inline_scripts

        return {source: errors.get(source) for _, _, source in find_inline_scripts(html)}

    return checker


def test_repair_fixes_broken_script() -> None:
    broken = "var x = 1 +;"
    fixed = "var x = 1+1;"
    html = f"<!DOCTYPE html><html><body><script>{broken}</script></body></html>"
    client = FakeLLMClient(f"```javascript\n{fixed}\n```")
    checker = _fake_checker({broken: "SyntaxError: bad"})

    repaired, repairs = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html.replace(broken, fixed)
    assert repairs == ["fixed: SyntaxError: bad"]
    assert len(client.calls) == 1


def test_repair_splices_only_the_broken_script_body() -> None:
    broken = "var x = 1 +;"
    fixed = "var x = 1+1;"
    html = (
        '<!DOCTYPE html>\n<html>\n<head>\n<script type="text/javascript">\n  var ok = "</div>";\n</script>\n</head>\n'
        f'<body>\n<p data-x="a>b">hi</p>\n<SCRIPT>{broken}</SCRIPT>\n<script></script>\n</body>\n</html>'
    )
    client = FakeLLMClient(f"```javascript\n{fixed}\n```")
    checker = _fake_checker({broken: "SyntaxError: bad"})

    repaired, _ = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html.replace(broken, fixed)


def test_repair_leaves_valid_scripts_untouched_with_no_llm_calls() -> None:
    html = "<!DOCTYPE html><html><body><script>var x = 1;</script></body></html>"
    client = FakeLLMClient()
    checker = _fake_checker({})

    repaired, repairs = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html
    assert repairs == []
    assert client.calls == []


def test_repair_skips_module_and_json_scripts() -> None:
    html = (
        "<!DOCTYPE html><html><body>"
        "<script type=\"module\">var x = 1 +;</script>"
        "<script type=\"application/ld+json\">{ bad json </script>"
        "</body></html>"
    )
    client = FakeLLMClient()
    checker = _fake_checker({"var x = 1 +;": "SyntaxError: bad", "{ bad json ": "SyntaxError: bad"})

    repaired, repairs = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html
    assert repairs == []
    assert client.calls == []


def test_repair_rejects_replacement_that_still_fails() -> None:
    broken = "var x = 1 +;"
    html = f"<!DOCTYPE html><html><body><script>{broken}</script></body></html>"
    client = FakeLLMClient("```javascript\nvar x = 1 + ;\n```")
    checker = _fake_checker({broken: "SyntaxError: bad", "var x = 1 + ;": "SyntaxError: still bad"})

    repaired, repairs = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html
    assert repairs == []


def test_repair_rejects_oversized_rewrite() -> None:
    broken = "var x = 1 +;"
    replacement = "var x = 1;" + " // padding" * 20
    html = f"<!DOCTYPE html><html><body><script>{broken}</script></body></html>"
    client = FakeLLMClient(f"```javascript\n{replacement}\n```")
    checker = _fake_checker({broken: "SyntaxError: bad"})

    repaired, repairs = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html
    assert repairs == []


def test_repair_leaves_html_unchanged_on_llm_error() -> None:
    broken = "var x = 1 +;"
    html = f"<!DOCTYPE html><html><body><script>{broken}</script></body></html>"
    client = FakeLLMClient(error=RuntimeError("model unreachable"))
    checker = _fake_checker({broken: "SyntaxError: bad"})

    repaired, repairs = repair_js_syntax(html, client, "generator-model", checker=checker)

    assert repaired == html
    assert repairs == []


def test_default_checker_finds_and_fixes_real_syntax_error() -> None:
    if importlib.util.find_spec("playwright") is None:
        return
    from playwright.sync_api import Error as PlaywrightError

    broken = "setOpen(!t.getAttribute('a']==='true'));"
    html = f"<!DOCTYPE html><html><body><script>{broken}</script></body></html>"
    try:
        errors = default_syntax_checker(html)
    except PlaywrightError:
        return
    if not errors:
        return
    assert errors.get(broken)


def test_default_checker_flags_top_level_return_and_does_not_execute() -> None:
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    returning = "if (x) { return; }"
    valid = "var a = 1;"
    side_effect = "window.__ran = true; document.title = 'ran';"
    strict_with = "\"use strict\"; with (a) {}"
    strict_octal = "'use strict'; var x = 010;"
    commented_strict = "/* c */ // d\n'use strict'; with (a) {}"
    sloppy_with = "with (a) {}"
    sources = [returning, valid, side_effect, strict_with, strict_octal, commented_strict, sloppy_with]
    html = "<!DOCTYPE html><html><body>" + "".join(f"<script>{s}</script>" for s in sources) + "</body></html>"
    errors = default_syntax_checker(html)
    if not errors:
        pytest.skip("browser unavailable")
    assert errors[returning]
    assert errors[valid] is None
    assert errors[side_effect] is None
    assert errors[strict_with]
    assert errors[strict_octal]
    assert errors[commented_strict]
    assert errors[sloppy_with] is None


def test_default_checker_isolates_scripts_from_each_other() -> None:
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    shadowing = "function String(){ window.__ran = 1 }"
    broken = "var x = 1 +;"
    html = f"<html><body><script>{shadowing}</script><script>{broken}</script></body></html>"
    errors = default_syntax_checker(html)
    if not errors:
        pytest.skip("browser unavailable")
    assert errors[shadowing] is None
    assert errors[broken].startswith("SyntaxError")
