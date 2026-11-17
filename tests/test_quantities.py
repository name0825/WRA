from types import SimpleNamespace

from wra.core.iteration import _dropped_quantity_findings
from wra.validator.quantities import DROPPED_QUANTITY_PREFIX, FAQ_QUESTION_PREFIX, _quantities, find_dropped_questions, find_dropped_quantities


def _page(html: str, created: bool = False) -> SimpleNamespace:
    return SimpleNamespace(html=html, created=created)


def _keys(text: str) -> list[tuple[str, str]]:
    return [key for key, _ in _quantities(text)]


def test_spaced_and_unspaced_quantities_share_a_key():
    assert _keys("800 lm") == _keys("800lm") == [("800", "lm")]
    assert find_dropped_quantities("<p>800lm</p>", "<p>800 lm</p>") == []
    assert find_dropped_quantities("<p>800 lm</p>", "<p>800 lm</p>") == []


def test_whitespace_between_number_and_unit_is_collapsed():
    source = "<p>800 lm</p>"
    assert find_dropped_quantities("<span>800</span> <span>lm</span>", source) == []
    assert find_dropped_quantities("<p>800\n      lm</p>", source) == []
    assert find_dropped_quantities("<p>800</p>\n\n<p>lm</p>", source) == []


def test_thousands_commas_and_decimals_are_normalized():
    assert _keys("5,600K") == [("5600", "K")]
    assert _keys("1.90 kg") == [("1.9", "kg")]
    assert find_dropped_quantities("<p>5600 K</p>", "<p>5,600K</p>") == []


def test_range_yields_both_endpoints():
    assert _keys("2,700-5,600 K") == [("2700", "K"), ("5600", "K")]
    assert find_dropped_quantities("<p>2,700K ~ 5,600K</p>", "<p>2,700-5,600 K</p>") == []
    assert len(find_dropped_quantities("<p>2,700K</p>", "<p>2,700-5,600 K</p>")) == 1


def test_dates_phones_and_versions_are_not_quantities():
    assert _keys("2026.08 and 2026년 8월 and 2026년 and 2026-08-15") == []
    assert _keys("010-1234-5678 and 02-123-4567 and v2.1 and 1.2.3 and iPhone15") == []
    assert _keys("Core i7-1260P and RTX 3080 Ti") == []


def test_korean_units_do_not_split_longer_units():
    assert _keys("12개월 3개 2시간 389,000원 15%") == [("12", "개월"), ("3", "개"), ("2", "시간"), ("389000", "원"), ("15", "%")]
    assert _keys("25°C") == [("25", "°")]


def test_dropped_quantity_is_reported_and_present_is_not():
    findings = find_dropped_quantities("<p>Weighs 2 kg</p>", "<p>Weighs 1.9 kg and gives 800 lm</p>")
    assert [finding.split("'")[1] for finding in findings] == ["1.9 kg", "800 lm"]
    assert all(finding.startswith(DROPPED_QUANTITY_PREFIX) for finding in findings)
    assert find_dropped_quantities("<p>800 lm 1.9 kg</p>", "<p>1.9 kg and 800 lm</p>") == []


def test_repeated_source_quantity_needs_one_page_match():
    source = "<nav>800 lm</nav><p>800 lm</p><p>800 lm</p>"
    assert find_dropped_quantities("<p>800 lm</p>", source) == []
    assert len(find_dropped_quantities("<p>x</p>", source)) == 1


def test_empty_source_and_script_quantities_are_ignored():
    assert find_dropped_quantities("<p>x</p>", "  ") == []
    assert find_dropped_quantities("<p>x</p>", "<script>var w='800 lm'</script><p>hi</p>") == []


def test_dropped_summary_question_is_reported_and_present_is_not():
    source = "<details><summary>배송은 얼마나 걸리나요?</summary><p>2일</p></details>"
    findings = find_dropped_questions("<p>x</p>", source)
    assert len(findings) == 1
    assert findings[0].startswith(FAQ_QUESTION_PREFIX)
    assert "배송은 얼마나 걸리나요?" in findings[0]
    assert find_dropped_questions("<h3>배송은 얼마나 걸리나요?</h3>", source) == []


def test_question_match_ignores_case_whitespace_and_punctuation():
    source = "<details><summary>Can I  return it?</summary></details>"
    assert find_dropped_questions("<p>can i return it</p>", source) == []
    assert find_dropped_questions("<p>Can I return it？</p>", source) == []


def test_question_headings_terms_and_buttons_need_a_question_mark_but_summary_does_not():
    source = "<h2>Shipping?</h2><dt>Refunds？</dt><button>Is it waterproof?</button><h3>About us</h3><button>Buy</button><details><summary>Warranty</summary></details>"
    asked = [finding.split("'")[1] for finding in find_dropped_questions("<p>x</p>", source)]
    assert asked == ["Shipping?", "Refunds?", "Is it waterproof?", "Warranty"]


def test_repeated_question_is_reported_once_and_script_questions_are_ignored():
    source = "<nav><h2>Shipping?</h2></nav><h2>Shipping?</h2><script>'<h2>Hidden?</h2>'</script>"
    assert len(find_dropped_questions("<p>x</p>", source)) == 1
    assert find_dropped_questions("<p>x</p>", "  ") == []


def test_helper_is_inert_above_medium_and_for_created_pages():
    page = _page("<p>800 lm</p><details><summary>Why?</summary></details>")
    assert len(_dropped_quantity_findings(page, "<p>x</p>", "low")) == 2
    assert len(_dropped_quantity_findings(page, "<p>x</p>", "medium")) == 2
    for level in ("high", "ultra", "madmax"):
        assert _dropped_quantity_findings(page, "<p>x</p>", level) == []
    assert _dropped_quantity_findings(_page(page.html, created=True), "<p>x</p>", "low") == []
