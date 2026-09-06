import pytest

from wra.core.models import Button, Content, FeatureModel, Heading
from wra.validator.tokens import find_corrupted_tokens


def make_features() -> FeatureModel:
    return FeatureModel(
        content=Content(
            headings=[
                Heading(level=1, text="FASTENER · BEARING · CUTTING · MRO"),
                Heading(level=2, text="취급품목 안내"),
                Heading(level=3, text="2021.11 제3회 캠프"),
                Heading(level=4, text="2022.11 제4회 캠프"),
            ],
            buttons=[Button(text="480-88-03967", href="#biz")],
        )
    )


def test_reports_a_corrupted_uppercase_token() -> None:
    findings = find_corrupted_tokens("<p>FASTER · BEARING · CUTTING · MRO</p>", make_features())

    assert any("FASTENER" in finding and "FASTER" in finding for finding in findings)


def test_reports_a_corrupted_cjk_token() -> None:
    findings = find_corrupted_tokens("<p>취급목목 안내</p>", make_features())

    assert any("취급품목" in finding and "취급목목" in finding for finding in findings)


def test_reports_a_corrupted_identifier() -> None:
    findings = find_corrupted_tokens("<p>480-88-03968</p>", make_features())

    assert any("480-88-03967" in finding for finding in findings)


def test_accepts_a_dropped_token() -> None:
    assert find_corrupted_tokens("<p>BEARING · CUTTING</p>", make_features()) == []


def test_accepts_reworded_prose() -> None:
    assert find_corrupted_tokens("<p>Precision components, sourced and quoted fast.</p>", make_features()) == []


def test_does_not_confuse_two_distinct_source_tokens() -> None:
    html = "<p>2021.11 제3회 캠프</p><p>2022.11 제4회 캠프</p>"

    assert find_corrupted_tokens(html, make_features()) == []


def test_ignores_script_and_style_content() -> None:
    assert find_corrupted_tokens("<script>var x = 'FASTER';</script>", make_features()) == []


def test_ignores_typographic_hyphen_variants() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=2, text="TA‑01 / TA‑02")]))

    assert find_corrupted_tokens("<p>TA-01 / TA-02</p>", features) == []


def korean_features() -> FeatureModel:
    """Source strings whose Korean tails move when a sentence is rewritten."""
    return FeatureModel(
        content=Content(
            headings=[
                Heading(level=1, text="T-Aaron은 정밀 절삭공구 전문기업입니다"),
                Heading(level=2, text="대성정밀은 신속하고 정확한 납기를 확보하고 있습니다"),
                Heading(level=3, text="산업자재 취급품목"),
                Heading(level=4, text="과학기술정보통신부장관상 수상"),
            ],
            buttons=[Button(text="480-88-03967", href="#biz")],
        )
    )


def test_accepts_a_particle_change_on_a_preserved_name() -> None:
    """`T-Aaron은` and `T-Aaron과` are one name carrying different grammar."""
    findings = find_corrupted_tokens("<p>T-Aaron과 함께합니다</p>", korean_features())
    assert findings == []


def test_accepts_a_verb_ending_change() -> None:
    findings = find_corrupted_tokens("<p>신속하게 확보하며 확인하시고</p>", korean_features())
    assert findings == []


def test_accepts_a_particle_appended_to_a_bare_noun() -> None:
    findings = find_corrupted_tokens("<p>산업자재와 공구</p>", korean_features())
    assert findings == []


def test_still_reports_a_dropped_syllable_in_a_proper_noun() -> None:
    """Inflection must not excuse an award name losing a character."""
    findings = find_corrupted_tokens("<p>과학기술정보통신부장상 수상</p>", korean_features())
    assert any("과학기술정보통신부장관상" in finding for finding in findings)


def test_still_reports_a_stem_that_changed_meaning() -> None:
    findings = find_corrupted_tokens("<p>취급목목 안내</p>", korean_features())
    assert any("취급품목" in finding for finding in findings)


def test_still_reports_a_changed_digit_in_an_identifier() -> None:
    findings = find_corrupted_tokens("<p>480-88-03968</p>", korean_features())
    assert any("480-88-03967" in finding for finding in findings)


# --- authored prose is not damage -------------------------------------------
#
# Every case below was observed in a real madmax run, where the gate turned the
# finding into an overall score of 4.00 and cost the iteration.

def _finding(source: str, rendered: str) -> list[str]:
    features = FeatureModel(content={"headings": [Heading(level=1, text=source)]})
    return find_corrupted_tokens(f"<html><body><p>{rendered}</p></body></html>", features)


@pytest.mark.parametrize("source, rendered", [
    ("드리겠습니다", "답변드리겠습니다"),   # the source survives whole inside a longer word
    ("있습니다", "같습니다"),               # two ordinary predicates, one character apart
    ("공급하여", "취급하여"),
])
def test_rewritten_korean_prose_is_not_reported(source, rendered) -> None:
    assert _finding(source, rendered) == []


@pytest.mark.parametrize("source, rendered", [
    ("취급품목", "취업품목"),
    ("대성정밀", "대성정민"),
    ("과학기술정보통신부장관상", "과학기술정보통신부장상"),
    ("과학기술정보통신부장관상", "과학기술정보통신부장관"),   # a name with its tail cut off
])
def test_a_damaged_name_is_still_reported(source, rendered) -> None:
    findings = _finding(source, rendered)
    assert findings and source in findings[0]


def test_an_expansion_is_excused_but_a_dropped_character_is_not() -> None:
    """The containment rule is one-directional on purpose.

    A longer word built around the source keeps every character of it. A source
    with a character missing is what damage looks like, so containment must not
    excuse that direction. (A token dropped wholesale is outside this detector:
    it reports near-misses, and absence is allowed by content freedom.)
    """
    # A one-character suffix is close enough to trip the similarity rule, so
    # this case is decided by containment alone.
    assert _finding("대성정밀", "대성정밀사") == []
    assert _finding("품질관리", "품질관리시스템") == []
    assert _finding("품질관리시스템", "품질관리스템") != []
