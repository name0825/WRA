from types import SimpleNamespace

from wra.core.iteration import _dropped_quantity_findings
from wra.validator.quantities import DROPPED_QUANTITY_PREFIX, FAQ_QUESTION_PREFIX, _quantities, find_dropped_questions, find_dropped_quantities


def _page(html: str, created: bool = False) -> SimpleNamespace:
    return SimpleNamespace(html=html, created=created)


def _keys(text: str) -> list[tuple[str, str]]:
    return [key for key, _ in _quantities(text, strict=True)]


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


def test_question_headings_terms_buttons_and_summaries_need_a_question_ending():
    source = "<h2>Shipping?</h2><dt>Refunds？</dt><button>Is it waterproof?</button><h3>About us</h3><button>Buy</button><details><summary>Warranty</summary></details>"
    asked = [finding.split("'")[1] for finding in find_dropped_questions("<p>x</p>", source)]
    assert asked == ["Shipping?", "Refunds?", "Is it waterproof?"]


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


def test_hangul_after_unit_counts_only_for_particles():
    assert _keys("3개를 10분간 11W의 5명이 1,000원에 3시간대") == [("3", "개"), ("10", "분"), ("11", "W"), ("5", "명"), ("1000", "원"), ("3", "시간")]
    assert _keys("1분기 3 원칙 USB 3.0 A타입 3개국") == []


def test_minutes_after_hour_are_not_quantities():
    assert find_dropped_quantities("<p>14:30</p>", "<p>14시 30분</p>") == []
    assert _keys("14시 30분") == []
    assert _keys("수원시 30분 거리") == [("30", "분")]


def test_spaced_single_letter_unit_after_latin_word_is_not_a_quantity():
    assert _keys("Plan 2 V") == []
    assert _keys("최대 소비전력 11 W") == [("11", "W")]
    assert _keys("Power: 11 W and 11W") == [("11", "W"), ("11", "W")]


def test_summary_without_question_ending_is_not_a_question():
    assert find_dropped_questions("<p>x</p>", "<details><summary>더 보기</summary><p>내용</p></details>") == []
    assert find_dropped_questions("<p>x</p>", "<details><summary>Show more</summary></details>") == []
    findings = find_dropped_questions("<p>x</p>", "<details><summary>배송은 얼마나 걸리나요</summary><p>2일</p></details>")
    assert len(findings) == 1
    assert "배송은 얼마나 걸리나요" in findings[0]


def test_dt_with_omitted_end_tag_does_not_merge_with_next_term():
    findings = find_dropped_questions("<p>x</p>", "<dl><dt>Q1?<dt>Q2?<dd>A</dl>")
    assert [finding.split("'")[1] for finding in findings] == ["Q1?", "Q2?"]


def test_source_only_skip_rules_leave_page_extraction_lenient():
    assert find_dropped_quantities("<p>최대 30%할인</p>", "<p>최대 30% 할인</p>") == []
    assert find_dropped_quantities("<p>AC 220 V</p>", "<p>220V</p>") == []
    assert find_dropped_quantities("<p>2시간마다</p>", "<p>2시간</p>") == []


def test_classed_question_divs_are_source_questions():
    source = '<div class="faq-item"><div class="faq-question"><span>Is it waterproof?</span></div><div class="faq-answer">Yes.</div></div><div class="faq-question">어떻게 교체하나요</div>'
    asked = [finding.split("'")[1] for finding in find_dropped_questions("<p>x</p>", source)]
    assert asked == ["Is it waterproof?", "어떻게 교체하나요"]


def test_classed_question_must_end_like_a_question_and_container_classes_are_ignored():
    source = '<div class="faq-questions"><p>Shipping?</p></div><div class="faq-question">Shipping policy</div><div class="question-box">Why?</div>'
    assert find_dropped_questions("<p>x</p>", source) == []


def test_classed_question_is_lenient_on_the_page_side():
    source = '<div class="faq-question">Is it waterproof?</div>'
    assert find_dropped_questions("<section><h3>IS IT WATERPROOF</h3></section>", source) == []
    assert len(find_dropped_questions("<p>Does it float?</p>", source)) == 1


def _fact_page(html: str, created: bool = False) -> SimpleNamespace:
    from wra.core.models import FeatureModel

    return SimpleNamespace(html=html, created=created, features=FeatureModel())


def test_fact_loss_findings_exist_only_where_coverage_is_not_enforced():
    from wra.core.iteration import collect_fact_loss_findings

    page = _fact_page('<p>800 lm</p><div class="faq-question">Why?</div>')
    for level in ("low", "medium"):
        assert collect_fact_loss_findings(page, "<p>x</p>", level) == []
    for level in ("high", "ultra", "madmax"):
        assert len(collect_fact_loss_findings(page, "<p>x</p>", level)) == 2
    assert collect_fact_loss_findings(_fact_page(page.html, created=True), "<p>x</p>", "madmax") == []


def test_fact_loss_findings_never_reach_the_hard_factual_cap_inputs():
    from wra.core.iteration import collect_content_findings

    page = _fact_page('<p>800 lm</p><div class="faq-question">Why?</div>')
    for level in ("high", "ultra", "madmax"):
        findings = collect_content_findings(page, "<p>x</p>", level)
        assert not [finding for finding in findings if finding.startswith((DROPPED_QUANTITY_PREFIX, FAQ_QUESTION_PREFIX))]


def test_classed_wrapper_with_a_question_heading_yields_only_the_heading():
    source = '<div class="faq-question"><h3>환불 되나요?</h3><p>7일 이내 가능해요</p></div>'
    asked = [finding.split("'")[1] for finding in find_dropped_questions("<h3>환불 되나요?</h3><p>일주일 안에 가능합니다</p>", source)]
    assert asked == []
    assert [finding.split("'")[1] for finding in find_dropped_questions("<p>x</p>", source)] == ["환불 되나요?"]


def test_classed_text_with_a_question_mark_must_end_with_it_and_unclosed_paragraphs_do_not_absorb_the_rest():
    assert find_dropped_questions("<p>x</p>", '<div class="faq-question">환불 되나요? 네 가능해요</div>') == []
    source = '<p class="question">Is it waterproof?<p>Yes, it is rated IP67 and we ship worldwide to many countries, which is why customers like it so much.<p class="question">Can I return it?'
    assert [finding.split("'")[1] for finding in find_dropped_questions("<p>x</p>", source)] == ["Is it waterproof?", "Can I return it?"]
