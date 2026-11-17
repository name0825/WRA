from types import SimpleNamespace

from wra.core.iteration import _dropped_date_findings
from wra.validator.dates import DROPPED_DATE_PREFIX, date_keys, find_dropped_dates


def _page(html: str, created: bool = False) -> SimpleNamespace:
    return SimpleNamespace(html=html, created=created)


def test_parses_numeric_korean_and_english_dates_to_the_same_keys():
    assert date_keys("2026.08") == [(2026, 8, None)]
    assert date_keys("2026-08-15 and 2026/8/5") == [(2026, 8, 15), (2026, 8, 5)]
    assert date_keys("2026년 8월 15일") == [(2026, 8, 15)]
    assert date_keys("2026년 8월") == [(2026, 8, None)]
    assert date_keys("Aug 2026") == [(2026, 8, None)]
    assert date_keys("August 12, 2026") == [(2026, 8, 12)]
    assert date_keys("Sept. 3rd, 2026") == [(2026, 9, 3)]
    assert date_keys("12 Aug 2026") == [(2026, 8, 12)]


def test_year_alone_versions_and_invalid_months_are_not_dates():
    assert date_keys("Founded in 2026") == []
    assert date_keys("v2026.1 and 2026.08.15.1 and 010-2026-08-15") == []
    assert date_keys("2026.13 and 2026.08.40") == []


def test_reformatted_date_is_not_flagged():
    assert find_dropped_dates("<p>2026년 8월</p>", "<p>2026.08</p>") == []


def test_month_only_source_is_satisfied_by_a_full_date():
    assert find_dropped_dates("<p>2026-08-15</p>", "<p>2026.08</p>") == []


def test_full_date_source_is_not_satisfied_by_a_month_only_page():
    findings = find_dropped_dates("<p>2026.08</p>", "<p>2026.08.15</p>")

    assert len(findings) == 1
    assert findings[0].startswith(DROPPED_DATE_PREFIX)
    assert "'2026.08.15'" in findings[0]


def test_a_date_repeated_in_the_source_needs_to_appear_only_once():
    assert find_dropped_dates("<p>2026.08 launch</p>", "<p>2026.08 launch</p><p>2026.08 review</p>") == []


def test_each_distinct_source_date_must_be_on_the_page():
    findings = find_dropped_dates("<p>2026.08.15</p>", "<p>2026.08</p><p>2026.08.15</p><p>2025.02</p>")

    assert findings == ["dropped source date: '2025.02' is in the source but not on the page"]


def test_spaced_dotted_korean_form_is_a_date():
    assert date_keys("2026. 8. 15.") == [(2026, 8, 15)]
    assert find_dropped_dates("<p>2026. 8. 15.</p>", "<p>2026.08.15</p>") == []
    assert date_keys("Total 2026. 12 students") == []


def test_date_ranges_yield_both_dates():
    for separator in ("-", "–", "~", " - "):
        assert date_keys(f"2025.03{separator}2026.08") == [(2025, 3, None), (2026, 8, None)]
    assert date_keys("2025.03.01-2026.08.31") == [(2025, 3, 1), (2026, 8, 31)]
    assert date_keys("010-2026-1234 and 82-2026-08") == []


def test_empty_source_and_script_dates_are_ignored():
    assert find_dropped_dates("<p>x</p>", "  ") == []
    assert find_dropped_dates("<p>x</p>", "<script>var d='2026.08.15'</script><p>hi</p>") == []
    assert find_dropped_dates("<script>var d='2026.08'</script>", "<p>2026.08</p>") != []


def test_created_pages_are_not_checked():
    assert _dropped_date_findings(_page("", created=True), "<p>x</p>") == []
    assert len(_dropped_date_findings(_page("<p>2026.08</p>"), "<p>x</p>")) == 1
