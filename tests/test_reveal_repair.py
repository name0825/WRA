import importlib.util

import pytest

from wra.generator.reveal_repair import repair_js_off_hidden

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")

_PARAGRAPH = "Reliable repair of hidden content keeps every section readable without scripts. " * 3


def _page(style: str, body: str, script: str = "", head: str = "", header: str = "") -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; }}
  {style}
</style>
{head}
</head>
<body>
{header}
<main>
{body}
</main>
{script}
</body>
</html>"""


def _reveal_html() -> str:
    sections = "\n".join(f'<section class="reveal"><p>{_PARAGRAPH}</p></section>' for _ in range(3))
    script = "<script>document.querySelectorAll('.reveal').forEach(el => el.classList.add('visible'));</script>"
    return _page(".reveal { opacity: 0; } .reveal.visible { opacity: 1; }", sections, script)


def test_reveal_hidden_without_js_is_repaired():
    html = _reveal_html()

    repaired, repairs = repair_js_off_hidden(html)

    assert repaired != html
    assert "<noscript><style>.reveal{opacity:1!important" in repaired
    assert len(repairs) == 1 and ".reveal" in repairs[0]
    assert repaired.replace(repaired[repaired.index("<noscript>"):repaired.index("</noscript>") + len("</noscript>")] + "\n", "") == html


def test_visible_page_is_unchanged():
    html = _page("", f"<section><p>{_PARAGRAPH}</p></section>" * 3)

    assert repair_js_off_hidden(html) == (html, [])


def test_second_run_is_idempotent():
    repaired, _ = repair_js_off_hidden(_reveal_html())

    assert repair_js_off_hidden(repaired) == (repaired, [])


def test_display_none_content_is_unchanged():
    html = _page(".tab { display: none; }", f"<section><p>{_PARAGRAPH}</p></section>" + f'<div class="tab"><p>{_PARAGRAPH}</p></div>' * 3)

    assert repair_js_off_hidden(html) == (html, [])


def test_class_less_culprit_falls_back_to_id():
    sections = "\n".join(f'<section id="s{i}" style="opacity:0"><p>{_PARAGRAPH}</p></section>' for i in range(3))
    html = _page("", sections)

    repaired, repairs = repair_js_off_hidden(html)

    assert "#s0,#s1,#s2{opacity:1!important" in repaired
    assert len(repairs) == 1


def _block(html: str) -> str:
    return html[html.index("<noscript>"):html.index("</noscript>")]


def test_shared_utility_class_that_is_not_only_culprits_is_not_chosen():
    sections = "\n".join(f'<section id="s{i}" class="opacity-0"><p>{_PARAGRAPH}</p></section>' for i in range(3))
    header = f'<header><div class="opacity-0 dropdown"><p>{_PARAGRAPH}</p></div></header>'
    style = ".opacity-0 { opacity: 0; } .dropdown { position: absolute; opacity: 0; visibility: hidden; }"
    html = _page(style, sections, header=header)

    repaired, repairs = repair_js_off_hidden(html)

    assert len(repairs) == 1
    assert "#s0,#s1,#s2{" in _block(repaired)
    assert ".opacity-0" not in _block(repaired)


def _fake_measure(classes_by_call, hidden_by_call):
    calls = []

    def measure(page):
        index = len(calls)
        calls.append(index)
        classes = classes_by_call[index]
        return {"total": 1000, "hidden": hidden_by_call[index], "selectors": [], "culprits": [{"id": "", "classes": [classes]}] if classes else [], "counts": {classes: 1} if classes else {}}

    return measure


def test_later_round_selectors_are_combined_into_one_block(monkeypatch):
    html = _reveal_html()
    monkeypatch.setattr("wra.generator.reveal_repair._measure", _fake_measure(["a", "b", None], [1000, 500, 0]))

    repaired, repairs = repair_js_off_hidden(html)

    assert repaired.count("<noscript>") == 1
    assert "<noscript><style>.a,.b{" in repaired
    assert len(repairs) == 1 and ".a, .b" in repairs[0]


def test_every_round_failing_returns_the_original(monkeypatch):
    html = _reveal_html()
    monkeypatch.setattr("wra.generator.reveal_repair._measure", _fake_measure(["a", "b", "c", "d"], [1000] * 4))

    assert repair_js_off_hidden(html) == (html, [])


def test_nested_hidden_ancestors_are_both_patched():
    inner = "\n".join(f'<div class="stagger"><p>{_PARAGRAPH}</p></div>' for _ in range(3))
    html = _page(".reveal, .stagger { opacity: 0; }", f'<section class="reveal">{inner}</section>')

    repaired, repairs = repair_js_off_hidden(html)

    assert len(repairs) == 1
    assert ".reveal" in _block(repaired) and ".stagger" in _block(repaired)
    assert repair_js_off_hidden(repaired) == (repaired, [])


def test_head_close_inside_script_string_is_not_the_injection_point():
    script = '<script>var s = "</head>";</script>'
    html = _page(".reveal { opacity: 0; }", "\n".join(f'<section class="reveal"><p>{_PARAGRAPH}</p></section>' for _ in range(3)), head=script)

    repaired, repairs = repair_js_off_hidden(html)

    assert len(repairs) == 1
    assert script in repaired
    assert repaired.index("<noscript>") > repaired.index(script) + len(script)
    assert repaired.index("<noscript>") < repaired.index("</head>", repaired.index(script) + len(script))
