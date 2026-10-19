import pytest

from wra.core.models import Button, Content, FeatureModel, Heading, Requirements, Section, Site, Structure
from wra.validator.tokens import _institution_skeleton, _is_numeric_reformat,find_corrupted_tokens, repair_corrupted_tokens, repair_dropped_numerals, repair_email_substitutions, repair_mid_token_substitutions, repair_particle_swaps, repair_translated_proper_nouns


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


def test_accepts_a_zero_width_character_inside_a_korean_word() -> None:
    findings = find_corrupted_tokens(
        "<p>가능성​분석 안내</p>",
        FeatureModel(content=Content(headings=[Heading(level=1, text="가능성분석")])),
    )

    assert findings == []


def test_accepts_reworded_prose() -> None:
    assert find_corrupted_tokens("<p>Precision components, sourced and quoted fast.</p>", make_features()) == []


def test_does_not_confuse_two_distinct_source_tokens() -> None:
    html = "<p>2021.11 제3회 캠프</p><p>2022.11 제4회 캠프</p>"

    assert find_corrupted_tokens(html, make_features()) == []


def test_ignores_site_descriptor_fields_as_source_text() -> None:
    features = FeatureModel(
        site=Site(type="landing", industry="B2B", brand={"voice": "신뢰감 있고 전문적인 B2B 파트너의 어조"}),
        content=Content(headings=[Heading(level=2, text="전문적인 서비스")]),
    )

    findings = find_corrupted_tokens("<h2>전문적인 파트너십</h2>", features)

    assert not any("파트너의" in finding for finding in findings)


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


def test_accepts_a_digit_restated_from_elsewhere_in_the_source() -> None:
    """`08:00` restated as `오전 8시부터` changes no digit the source never wrote."""
    features = FeatureModel(content=Content(text=["매일 새벽 5시부터 반죽을 시작", "토요일 08:00 ~ 21:00"]))

    findings = find_corrupted_tokens("<p>토요일은 오전 8시부터 만납니다</p>", features)

    assert not any("5시부터" in finding for finding in findings)


def test_still_reports_a_digit_absent_from_the_source() -> None:
    features = FeatureModel(content=Content(text=["매일 새벽 5시부터 반죽을 시작"]))

    findings = find_corrupted_tokens("<p>오후 7시부터 시작합니다</p>", features)

    assert any("5시부터" in finding and "7시부터" in finding for finding in findings)


def test_still_reports_a_digit_that_matches_an_unrelated_source_number() -> None:
    """A digit that only coincides with an unrelated address number is not a restatement."""
    features = FeatureModel(content=Content(text=["매일 새벽 5시부터 반죽을 시작", "주소: 서울특별시 은평구 행복로 42길 7"]))

    findings = find_corrupted_tokens("<p>오후 7시부터 시작합니다</p>", features)

    assert any("5시부터" in finding and "7시부터" in finding for finding in findings)


def test_still_reports_a_token_with_a_dropped_unit_character() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="2023년부터 지역 요양시설과")]))

    findings = find_corrupted_tokens("<p>2023부터 지역 요양시설과</p>", features)

    assert any("2023년부터" in finding and "2023부터" in finding for finding in findings)


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
    ("최적화를", "최적화된"),               # noun + particle rewritten as a 되다 derivation
    ("최적화", "최적화된"),
])
def test_rewritten_korean_prose_is_not_reported(source, rendered) -> None:
    assert _finding(source, rendered) == []


@pytest.mark.parametrize("source, rendered", [
    ("취급품목", "취업품목"),
    ("대성정밀", "대성정민"),
    ("과학기술정보통신부장관상", "과학기술정보통신부장상"),
    ("과학기술정보통신부장관상", "과학기술정보통신부장관"),   # a name with its tail cut off
    ("최적화를", "최적와를"),                 # a changed syllable inside the stem
    ("김박지한", "김박지해"),                 # two derivational tails are not one stem
    ("김박지한", "김박지신"),
    ("고정밀한", "고정밀할"),
    ("대성정밀한", "대성정밀"),
])
def test_a_damaged_name_is_still_reported(source, rendered) -> None:
    findings = _finding(source, rendered)
    assert findings and source in findings[0]


def _freedom_finding(source: str, rendered: str, freedom: str) -> list[str]:
    features = FeatureModel(content={"headings": [Heading(level=1, text=source)]})
    return find_corrupted_tokens(f"<html><body><p>{rendered}</p></body></html>", features, content_freedom=freedom)


@pytest.mark.parametrize("freedom, source, rendered", [
    ("madmax", "효율성을 극대화해 드립니다", "효율성을 극대화할 최적의 자재"),
    ("ultra", "가공을 제공해 드립니다", "가공을 제공할 서비스"),
    ("madmax", "최적화되어 있습니다", "최적화된 설비"),
])
def test_a_predicate_rewrite_is_excused_at_ultra_and_madmax(freedom, source, rendered) -> None:
    assert _freedom_finding(source, rendered, freedom) == []


@pytest.mark.parametrize("freedom, source, rendered", [
    ("low", "효율성을 극대화해 드립니다", "효율성을 극대화할 최적의 자재"),
    ("high", "효율성을 극대화해 드립니다", "효율성을 극대화할 최적의 자재"),
    ("madmax", "김박지한", "김박지해"),
    ("madmax", "김박지한 대표", "김박지해 대표"),
    ("madmax", "고정밀한 가공", "고정밀할 가공"),
    ("madmax", "고정밀한 볼트 가공", "고정밀할 볼트 가공"),
    ("madmax", "김박지한 보고서", "김박지해 보고서"),
    ("madmax", "극대화해 드립니다", "극대와해 드립니다"),
])
def test_a_predicate_rewrite_is_still_reported_elsewhere(freedom, source, rendered) -> None:
    assert _freedom_finding(source, rendered, freedom)


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


def test_a_corrupted_token_holding_a_backslash_survives_the_round_trip() -> None:
    """The finding string is parsed back out by the refiner; repr() escaping broke it."""
    from wra.refiner.refiner import HTMLRefiner

    source = r"취급품목\A"
    rendered = r"취업품목\A"
    features = FeatureModel()
    features.requirements.must_preserve = [source]
    html = f"<html><body><p>{rendered}</p></body></html>"

    findings = find_corrupted_tokens(html, features)
    assert findings, "the damaged token should be reported"

    repairs = HTMLRefiner.content_repairs(html, findings)
    assert repairs[0]["with"] == source
    assert repairs[0]["replace"] == rendered
    assert repairs[0]["occurrences"] != ["not found in the current page"]


# --- institution-name substitution ------------------------------------------

def _institution_features() -> FeatureModel:
    return FeatureModel(
        content=Content(text=["Yeungnam University(Information and Communication Engineering)"]),
        requirements=Requirements(optional=["Yeungnam University(Information and Communication Engineering)"]),
    )


def test_reports_an_institution_substituted_by_a_different_one() -> None:
    findings = find_corrupted_tokens("<p>경북대학교 (Information and Communication Engineering)</p>", _institution_features())

    assert len(findings) == 1
    assert "Yeungnam University" in findings[0]


def test_reports_an_institution_substituted_by_a_different_latin_one() -> None:
    findings = find_corrupted_tokens("<p>Hanyang University (Information and Communication Engineering)</p>", _institution_features())

    assert len(findings) == 1
    assert "Yeungnam University" in findings[0]


@pytest.mark.parametrize(
    ("stem_a", "stem_b"),
    [
        ("영남", "Yeungnam"),
        ("한양", "Hanyang"),
        ("고려", "Korea"),
        ("서울", "Seoul"),
    ],
)
def test_institution_skeleton_matches_across_script(stem_a: str, stem_b: str) -> None:
    assert _institution_skeleton(stem_a) == _institution_skeleton(stem_b)


def test_institution_skeleton_differs_for_different_institutions() -> None:
    assert _institution_skeleton("경북") != _institution_skeleton("Yeungnam")


def test_accepts_the_source_institution_named_only_in_its_korean_form() -> None:
    """The page merely wrote the same institution in Korean; this is not a
    substitution even though the two names share no characters.
    """
    assert find_corrupted_tokens("<p>영남대학교 (Information and Communication Engineering)</p>", _institution_features()) == []


def test_reports_a_korean_source_institution_substituted_by_a_different_one() -> None:
    features = FeatureModel(content=Content(text=["영남대학교 (Information and Communication Engineering)"]))

    findings = find_corrupted_tokens("<p>서울대학교 (Information and Communication Engineering)</p>", features)

    assert len(findings) == 1
    assert "영남대학교" in findings[0]


def test_accepts_the_source_institution_preserved_verbatim() -> None:
    assert find_corrupted_tokens("<p>Yeungnam University (Information and Communication Engineering)</p>", _institution_features()) == []


def test_accepts_dropping_the_institution_entirely() -> None:
    assert find_corrupted_tokens("<p>Information and Communication Engineering</p>", _institution_features()) == []


def test_accepts_a_preceding_word_wrongly_swept_into_the_source_core() -> None:
    """Flattened source text may put an unrelated word right before the real name;
    only the single word immediately before the keyword belongs to the core.
    """
    features = FeatureModel(
        content=Content(text=["MariaDB Yeungnam University"]),
        requirements=Requirements(optional=["High School"]),
    )

    assert find_corrupted_tokens("<p>High School</p>", features) == []


def test_accepts_a_page_core_that_does_not_cross_a_newline_inside_a_text_node() -> None:
    """A long run of capitalized words spanning a line break on the page is not
    one institution name; the core is just the word immediately before the keyword.
    """
    features = FeatureModel(
        content=Content(text=["About Contact MinJae Park Yeungnam University"]),
        requirements=Requirements(optional=["SW Future Talent High School"]),
    )
    html = "<p>Awards\nSW Future Talent High School</p>"

    assert find_corrupted_tokens(html, features) == []


def test_accepts_the_source_institution_present_verbatim_on_a_later_line() -> None:
    features = FeatureModel(content=Content(text=["About Contact MinJae Park Yeungnam University"]))
    html = "<p>Education\nYeungnam University</p>"

    assert find_corrupted_tokens(html, features) == []


# --- placeholder leaks and mangled numbers ----------------------------------

def test_reports_a_leaked_mangled_placeholder_token() -> None:
    findings = find_corrupted_tokens("<p>Contact WRATR9 for details.</p>", make_features())

    assert any("WRATR9" in finding and "leaked placeholder token" in finding for finding in findings)


def test_does_not_flag_a_separator_style_token_as_leaked() -> None:
    assert find_corrupted_tokens("<p>WRA-XL-2024</p>", make_features()) == []


@pytest.mark.parametrize("rendered", ["#202abc", "202abc"])
def test_accepts_a_hex_like_or_hash_prefixed_number(rendered: str) -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="Founded in 2024.")]))

    findings = find_corrupted_tokens(f"<p>{rendered}</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_number_with_a_vw_unit_suffix() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="Founded in 1000.")]))

    findings = find_corrupted_tokens("<p>100vw</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_reports_a_mangled_number_against_the_source() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="Founded in 2027.")]))

    findings = find_corrupted_tokens("<p>Founded in 202tr.</p>", features)

    assert any("2027" in finding and "202tr" in finding for finding in findings)


@pytest.mark.parametrize("rendered", ["100ms", "2027th", "24h"])
def test_accepts_numbers_with_unit_or_ordinal_suffixes(rendered) -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="Founded in 2027.")]))

    findings = find_corrupted_tokens(f"<p>{rendered}</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_mangled_looking_number_with_no_matching_source_number() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="No numbers here.")]))

    findings = find_corrupted_tokens("<p>202tr</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_second_source_institution_named_verbatim_while_the_first_is_abbreviated() -> None:
    """A source with two institutions must not be flagged just because one of them is
    abbreviated into a form the regex cannot see, while the other is rendered verbatim
    and looks like "another institution" to a check that only compares against the
    one institution it happens to be missing.
    """
    features = FeatureModel(
        content=Content(text=["Yeungnam University and Kyungpook National University collaborated on the project."]),
        requirements=Requirements(optional=[
            "Yeungnam University and Kyungpook National University collaborated on the project."
        ]),
    )
    html = "<p>영남대와 Kyungpook National University, 함께 공동으로 프로젝트를 진행했습니다.</p>"

    assert find_corrupted_tokens(html, features) == []


def test_flags_a_han_character_leaked_into_korean_text() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="딸기생크림")]))

    findings = find_corrupted_tokens("<p>딸기生크림입니다</p>", features)

    assert any("foreign script" in finding and "딸기生크림입니다" in finding for finding in findings)


def test_does_not_flag_a_han_character_present_in_the_source() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="(주)製造 산업자재")]))

    findings = find_corrupted_tokens("<p>(주)製造 산업자재를 취급합니다</p>", features)

    assert not any("foreign script" in finding for finding in findings)


def test_does_not_flag_han_characters_when_source_has_no_hangul() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="Precision components")]))

    findings = find_corrupted_tokens("<p>製造 components</p>", features)

    assert not any("foreign script" in finding for finding in findings)


def test_flags_latin_letters_glued_after_hangul() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="멀티모달 서비스")]))

    findings = find_corrupted_tokens("<p>멀티모adal 서비스</p>", features)

    assert any("foreign script" in finding and "멀티모adal" in finding for finding in findings)


def test_does_not_flag_latin_before_hangul() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="API를 HTML로 변환합니다")]))

    findings = find_corrupted_tokens("<p>API를 HTML로 변환합니다</p>", features)

    assert not any("foreign script" in finding for finding in findings)


def test_does_not_flag_a_hangul_latin_token_present_verbatim_in_source() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="멀티모dal 서비스")]))

    findings = find_corrupted_tokens("<p>멀티모dal 서비스</p>", features)

    assert not any("foreign script" in finding for finding in findings)


def _copyright_features() -> FeatureModel:
    return FeatureModel(content=Content(text=["Copyright 행복빵집. All rights reserved."]))


def test_reports_a_translated_proper_noun() -> None:
    html = "<p>Copyright happiness bakery. All rights reserved.</p>"

    findings = find_corrupted_tokens(html, _copyright_features())

    assert any("translated proper noun" in finding and "행복빵집" in finding and "happiness bakery" in finding for finding in findings)


def test_repairs_a_translated_proper_noun() -> None:
    html = "<p>Copyright happiness bakery. All rights reserved.</p>"

    repaired, applied = repair_translated_proper_nouns(html, _copyright_features())

    assert "Copyright 행복빵집. All rights reserved." in repaired
    assert applied == ["'happiness bakery' -> '행복빵집'"]


def test_accepts_the_untranslated_copyright_line() -> None:
    html = "<p>Copyright 행복빵집. All rights reserved.</p>"

    findings = find_corrupted_tokens(html, _copyright_features())

    assert not any("translated proper noun" in finding for finding in findings)

    repaired, applied = repair_translated_proper_nouns(html, _copyright_features())

    assert repaired == html
    assert applied == []


def test_accepts_a_translated_phrase_with_no_latin_context_in_the_source() -> None:
    features = FeatureModel(content=Content(text=["행복빵집"]))

    findings = find_corrupted_tokens("<p>happiness bakery</p>", features)

    assert not any("translated proper noun" in finding for finding in findings)


def test_does_not_flag_or_rewrite_when_hangul_run_is_at_segment_start() -> None:
    features = FeatureModel(content=Content(text=["행복빵집 Bakery"]))
    html = (
        "<p>This is a very long unrelated English sentence about something else "
        "entirely that happens to end with the word Bakery</p>"
    )

    findings = find_corrupted_tokens(html, features)
    assert not any("translated proper noun" in finding for finding in findings)

    repaired, applied = repair_translated_proper_nouns(html, features)
    assert repaired == html
    assert applied == []


def test_reports_a_dropped_native_korean_numeral() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="오후 세 시의 빛")]))

    findings = find_corrupted_tokens("<p>오후 시의 빛</p>", features)

    assert any("오후 세 시의" in finding and "오후 시의" in finding for finding in findings)


def test_reports_a_dropped_multiplier() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="5만 시간 뒤")]))

    findings = find_corrupted_tokens("<p>5시간 뒤</p>", features)

    assert any("5만 시간" in finding and "5시간" in finding for finding in findings)


def test_accepts_the_source_numeral_phrase_restated_verbatim() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="오후 세 시의 빛")]))

    findings = find_corrupted_tokens("<p>오후 세 시의 빛</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_numeral_phrase_that_is_simply_absent() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="오후 세 시의 빛")]))

    findings = find_corrupted_tokens("<p>완전히 다른 문장입니다</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_digit_word_token_also_present_in_the_source() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="5만 시간 뒤, 5시간 남았다")]))

    findings = find_corrupted_tokens("<p>5시간 남았다</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_numeral_straddling_a_sentence_boundary() -> None:
    features = FeatureModel(
        content=Content(
            headings=[Heading(level=1, text='Han, J. "Designing Socially Acceptable Delivery Robots." RO-MAN , 2023. Members')]
        )
    )

    findings = find_corrupted_tokens("<p>RO-MAN Members</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_accepts_a_normal_adjacent_pair_unrelated_to_numerals() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="빠른 배송 서비스")]))

    findings = find_corrupted_tokens("<p>빠른 서비스</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


def test_repairs_a_dropped_multiplier() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="5만 시간 뒤")]))

    repaired, applied = repair_dropped_numerals("<p>5시간 뒤</p>", features)

    assert "<p>5만 시간 뒤</p>" == repaired
    assert applied == ["'5시간' -> '5만 시간'"]


def test_repairs_a_dropped_numeral_word() -> None:
    features = FeatureModel(content=Content(text=["and 9 graduate programs"]))

    repaired, applied = repair_dropped_numerals("<p>and graduate programs</p>", features)

    assert "<p>and 9 graduate programs</p>" == repaired
    assert applied == ["'and graduate' -> 'and 9 graduate'"]


def test_does_not_repair_a_numeral_phrase_absent_from_the_source() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="오후 세 시의 빛")]))
    html = "<p>완전히 다른 문장입니다</p>"

    repaired, applied = repair_dropped_numerals(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_dropped_numerals_inside_a_script_or_style() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="5만 시간 뒤")]))
    html = '<style>.x::before{content:"5시간 뒤"}</style><script>var x = "5시간 뒤";</script>'

    repaired, applied = repair_dropped_numerals(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_dropped_numerals_in_an_attribute() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="5만 시간 뒤")]))
    html = '<a title="5시간 뒤">link</a>'

    repaired, applied = repair_dropped_numerals(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_an_ambiguous_multiplier_source() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="5만 시간 뒤, 5천 시간 뒤")]))
    html = "<p>5시간 뒤</p>"

    repaired, applied = repair_dropped_numerals(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_rewrite_translated_text_inside_noscript_or_template() -> None:
    features = _copyright_features()
    html = (
        "<noscript>Copyright happiness bakery. All rights reserved.</noscript>"
        "<template>Copyright happiness bakery. All rights reserved.</template>"
    )

    findings = find_corrupted_tokens(html, features)
    assert findings == []

    repaired, applied = repair_translated_proper_nouns(html, features)
    assert repaired == html
    assert applied == []


def test_does_not_rewrite_translated_text_inside_a_script_or_attribute() -> None:
    features = _copyright_features()
    html = '<a title="Copyright happiness bakery. All rights reserved.">link</a><script>var x = "Copyright happiness bakery. All rights reserved.";</script>'

    findings = find_corrupted_tokens(html, features)
    assert findings == []

    repaired, applied = repair_translated_proper_nouns(html, features)

    assert repaired == html
    assert applied == []


def test_quotes_the_whole_digit_run_when_a_multiplier_is_dropped() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="15만 시간 뒤")]))

    findings = find_corrupted_tokens("<p>5시간 뒤</p>", features)

    assert not any("'5만 시간'" in finding for finding in findings)


def test_accepts_a_dropped_word_that_is_only_a_sino_korean_homograph() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="우리 회사는 이 제품을 만든다")]))

    findings = find_corrupted_tokens("<p>우리 회사는 제품을 만든다</p>", features)

    assert not any("corrupted source token" in finding for finding in findings)


@pytest.mark.parametrize("source, rendered", [
    ("구워냅니다", "구워집니다"),
    ("색온도를", "색온드를"),
    ("바뀝니다", "바뀁니다"),
])
def test_repairs_a_one_character_corrupted_token(source, rendered) -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text=source)]))

    repaired, applied = repair_corrupted_tokens(f"<p>{rendered}</p>", features)

    assert repaired == f"<p>{source}</p>"
    assert applied == [f"'{rendered}' -> '{source}'"]


def test_does_not_report_an_added_word_when_the_source_token_survives_intact() -> None:
    features = FeatureModel(content=Content(text=["평일 오후 2시 전에 주문하면 당일 출고하며..."]))
    html = "<p>평일 오후 2시 전에 주문하면 당일 출고하며...</p><button>주문하기</button>"

    findings = find_corrupted_tokens(html, features)

    assert not any("주문하면" in finding for finding in findings)


def test_does_not_report_an_added_word_when_the_source_sentence_is_duplicated_across_views() -> None:
    sentence = "평일 오후 2시 전에 주문하면 당일 출고하며..."
    features = FeatureModel(
        content=Content(paragraphs=[sentence], text=[sentence]),
        structure=Structure(sections=[Section(tag="section", text=sentence)]),
    )
    html = f"<p>{sentence}</p><button>주문하기</button>"

    findings = find_corrupted_tokens(html, features)

    assert not any("주문하면" in finding for finding in findings)


def test_reports_corruption_inside_a_copied_sentence_when_an_intact_duplicate_exists_elsewhere() -> None:
    features = FeatureModel(content=Content(paragraphs=[
        "다이얼을 돌리면 밝기와 색온도를 손끝에서 맞춥니다",
        "다른 다이얼로도 색온도를 조절할 수 있습니다",
    ]))
    html = "<p>다이얼을 돌리면 밝기와 색온드를 손끝에서 맞춥니다</p><p>다른 다이얼로도 색온도를 조절할 수 있습니다</p>"

    findings = find_corrupted_tokens(html, features)

    assert any("'색온도를'" in finding and "'색온드를'" in finding for finding in findings)


def test_still_reports_corruption_when_the_source_token_is_absent_from_the_page() -> None:
    features = FeatureModel(content=Content(text=["주문하면 좋습니다"]))
    html = "<p>주분하면 좋습니다</p>"

    findings = find_corrupted_tokens(html, features)

    assert any("'주문하면'" in finding and "'주분하면'" in finding for finding in findings)


def test_reports_when_the_only_shared_neighbour_is_a_common_function_word() -> None:
    features = FeatureModel(content=Content(paragraphs=[
        "그리고 주문하면 좋습니다",
        "결제창에서 주문하면 완료됩니다",
    ]))
    html = "<p>그리고 주분하면 좋습니다</p><p>결제창에서 주문하면 완료됩니다</p>"

    findings = find_corrupted_tokens(html, features)

    assert any("'주문하면'" in finding and "'주분하면'" in finding for finding in findings)


def test_reports_when_only_the_successor_neighbour_matches() -> None:
    features = FeatureModel(content=Content(paragraphs=[
        "아침에 주문하면 좋습니다",
        "저녁에 주문하면 좋습니다",
    ]))
    html = "<p>지금 주분하면 좋습니다</p><p>저녁에 주문하면 좋습니다</p>"

    findings = find_corrupted_tokens(html, features)

    assert any("'주문하면'" in finding and "'주분하면'" in finding for finding in findings)


def test_does_not_repair_an_ambiguous_corrupted_token() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="구워냅니다 구워둡니다")]))
    html = "<p>구워집니다</p>"

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_a_token_whose_final_syllable_differs() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="문의하기")]))
    html = "<p>언제든 문의하면 답해 드립니다</p>"

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_a_korean_inflection_pair() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="대성정밀은")]))
    html = "<p>대성정밀의</p>"

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_a_corrupted_token_inside_a_script_or_attribute() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="구워냅니다")]))
    html = '<a title="구워집니다">link</a><script>var x = "구워집니다";</script>'

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_a_candidate_present_in_the_source_text() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="구워냅니다")], text=["구워집니다는 다른 뜻입니다"]))
    html = "<p>구워집니다</p>"

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_repairs_a_token_with_a_dropped_unit_character() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="2023년부터 지역 요양시설과")]))

    repaired, applied = repair_corrupted_tokens("<p>2023부터 지역 요양시설과</p>", features)

    assert repaired == "<p>2023년부터 지역 요양시설과</p>"
    assert applied == ["'2023부터' -> '2023년부터'"]


def test_does_not_repair_a_bare_number_against_a_source_with_a_unit() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="2023년부터 지역 요양시설과")]))
    html = "<p>2023 지역 요양시설과</p>"

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_does_not_repair_an_ambiguous_dropped_unit() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=1, text="2023년부터 2023월부터")]))
    html = "<p>2023부터</p>"

    repaired, applied = repair_corrupted_tokens(html, features)

    assert repaired == html
    assert applied == []


def test_repair_corrupted_tokens_is_a_no_op_when_nothing_is_corrupted() -> None:
    html = "<p>정상적인 문장입니다</p>"

    repaired, applied = repair_corrupted_tokens(html, make_features())

    assert repaired == html
    assert applied == []


def test_reports_a_particle_swapped_on_a_batchim_stem() -> None:
    features = FeatureModel(content=Content(text=["빵은 구입 후 당일 내에 드시는 것을 권장합니다"]))

    findings = find_corrupted_tokens("<p>빵는 구입 후 당일 내에 드십시오</p>", features)

    assert any("'빵은'" in finding and "'빵는'" in finding for finding in findings)


def test_reports_a_particle_swapped_the_other_direction() -> None:
    features = FeatureModel(content=Content(text=["모듈를 빼낸 자리에 새 부품을 끼운다"]))

    findings = find_corrupted_tokens("<p>모듈을 빼낸 자리에 새 부품을 끼운다</p>", features)

    assert findings == []


def test_repairs_a_particle_swap() -> None:
    features = FeatureModel(content=Content(text=["모듈을 빼낸 자리에 새 부품을 끼운다"]))

    repaired, applied = repair_particle_swaps("<p>모듈를 빼낸 자리에 새 부품을 끼운다</p>", features)

    assert "<p>모듈을 빼낸 자리에 새 부품을 끼운다</p>" == repaired
    assert applied == ["'모듈를' -> '모듈을'"]


def test_accepts_a_verb_ending_that_looks_like_a_swapped_particle() -> None:
    features = FeatureModel(content=Content(text=["쉬는 날에는 문을 닫습니다"]))

    findings = find_corrupted_tokens("<p>쉬는 날에는 문을 닫습니다</p>", features)

    assert not any("'쉬" in finding for finding in findings)


def test_reports_a_digit_swapped_between_matching_neighbors() -> None:
    features = FeatureModel(content=Content(text=["은평구청역 3번 출구에서 도보 5분"]))

    findings = find_corrupted_tokens("<p>은평구청역 2번 출구에서 도보 5분</p>", features)

    assert any("'3번'" in finding and "'2번'" in finding for finding in findings)


def test_reports_a_latin_function_word_swapped_between_matching_neighbors() -> None:
    features = FeatureModel(content=Content(text=["빵은 구입 후 당일 내에 드시는 것을 권장합니다"]))

    findings = find_corrupted_tokens("<p>빵은 구입 후 당일 내에 드시는 to 권장합니다</p>", features)

    assert any("'것을'" in finding and "'to'" in finding for finding in findings)


def test_accepts_a_rewritten_sentence_with_no_matching_neighbors() -> None:
    features = FeatureModel(content=Content(text=["은평구청역 3번 출구에서 도보 5분"]))

    findings = find_corrupted_tokens("<p>지하철역 인근에 위치해 찾아오기 편리합니다</p>", features)

    assert findings == []


def test_repairs_a_mid_token_substitution() -> None:
    features = FeatureModel(content=Content(text=["은평구청역 3번 출구에서 도보 5분"]))

    repaired, applied = repair_mid_token_substitutions("<p>은평구청역 2번 출구에서 도보 5분</p>", features)

    assert "<p>은평구청역 3번 출구에서 도보 5분</p>" == repaired
    assert applied == ["'2번' -> '3번'"]


def test_reports_an_email_local_part_change_with_a_matching_domain() -> None:
    features = FeatureModel(content=Content(text=["문의: hello@byeori.studio"]))

    findings = find_corrupted_tokens("<p>문의: info@byeori.studio</p>", features)

    assert any("'hello@byeori.studio'" in finding and "'info@byeori.studio'" in finding for finding in findings)


def test_accepts_a_new_email_on_a_different_domain() -> None:
    features = FeatureModel(content=Content(text=["문의: hello@byeori.studio"]))

    findings = find_corrupted_tokens("<p>문의: hello@newvendor.com</p>", features)

    assert findings == []


def test_repairs_an_email_local_part_change() -> None:
    features = FeatureModel(content=Content(text=["문의: hello@byeori.studio"]))

    repaired, applied = repair_email_substitutions("<p>문의: info@byeori.studio</p>", features)

    assert "<p>문의: hello@byeori.studio</p>" == repaired
    assert applied == ["'info@byeori.studio' -> 'hello@byeori.studio'"]


def test_ignores_an_email_domain_match_inside_a_script_tag() -> None:
    features = FeatureModel(content=Content(text=["문의: hello@byeori.studio"]))
    html = "<script>var e = 'info@byeori.studio';</script>"

    findings = find_corrupted_tokens(html, features)
    repaired, applied = repair_email_substitutions(html, features)

    assert findings == []
    assert repaired == html
    assert applied == []


def test_accepts_ro_after_a_rieul_final_stem() -> None:
    features = FeatureModel(content=Content(text=["서울로 놀러 오세요"]))

    findings = find_corrupted_tokens("<p>서울로 놀러 오세요</p>", features)

    assert findings == []


def test_reports_eu_ro_swapped_on_a_rieul_final_stem() -> None:
    features = FeatureModel(content=Content(text=["서울로 놀러 오세요"]))

    findings = find_corrupted_tokens("<p>서울으로 놀러 오세요</p>", features)

    assert any("'서울로'" in finding and "'서울으로'" in finding for finding in findings)


def test_repairs_eu_ro_swapped_on_a_rieul_final_stem() -> None:
    features = FeatureModel(content=Content(text=["서울로 놀러 오세요"]))

    repaired, applied = repair_particle_swaps("<p>서울으로 놀러 오세요</p>", features)

    assert "<p>서울로 놀러 오세요</p>" == repaired
    assert applied == ["'서울으로' -> '서울로'"]


def test_reports_eu_ro_swapped_on_a_non_rieul_batchim_stem() -> None:
    features = FeatureModel(content=Content(text=["책상으로 옮겨 주세요"]))

    findings = find_corrupted_tokens("<p>책상로 옮겨 주세요</p>", features)

    assert any("'책상으로'" in finding and "'책상로'" in finding for finding in findings)


def test_ignores_a_mid_token_substitution_spanning_sibling_nodes() -> None:
    features = FeatureModel(content=Content(text=["총 5개 항목이 있습니다."]))
    html = "<p>총 <b>3개</b> 항목이 있습니다.</p>"

    findings = find_corrupted_tokens(html, features)
    repaired, applied = repair_mid_token_substitutions(html, features)

    assert findings == []
    assert repaired == html
    assert applied == []


@pytest.mark.parametrize("source, rendered", [
    ("문의하기", "문의하면"),
    ("신청하기", "신청하면"),
])
def test_a_rewritten_final_ending_after_a_verb_stem_is_not_reported(source, rendered) -> None:
    assert _finding(source, rendered) == []


def test_a_final_syllable_change_in_a_name_is_still_reported() -> None:
    findings = _finding("대성정밀", "대성정민")
    assert findings and "대성정밀" in findings[0]


@pytest.mark.parametrize("source, rendered", [
    ("2023.05.01 개최", "2023.5.1 개최"),
    ("2023.05.01 개최", "2023.5.01 개최"),
    ("3,000원 부터", "3000원 부터"),
    ("2023/05/01 개최", "2023/5/1 개최"),
])
def test_numerically_equal_reformatting_is_not_reported(source, rendered) -> None:
    assert _finding(source, rendered) == []


@pytest.mark.parametrize("source, rendered", [
    ("2023.05.01 개최", "2023.05.10 개최"),
    ("2023.05.01 개최", "2023.05.02 개최"),
])
def test_a_changed_date_is_still_reported(source, rendered) -> None:
    assert _finding(source, rendered)


@pytest.mark.parametrize("source, rendered", [
    ("품질관리팀", "품질관리"),
    ("생산관리부", "생산관리"),
])
def test_dropping_a_trailing_generic_unit_from_a_compound_is_not_reported(source, rendered) -> None:
    assert _finding(source, rendered) == []


@pytest.mark.parametrize("source, rendered", [
    ("품질관리팀", "품질관리티"),
    ("품질관리원", "품질관리"),
])
def test_other_shortenings_of_a_compound_are_still_reported(source, rendered) -> None:
    assert _finding(source, rendered)


def test_a_thousands_separator_does_not_split_a_number() -> None:
    assert _finding("가격 3,000원", "가격 3,000원") == []
    findings = _finding("가격 3,000원", "가격 3,900원")
    assert findings and "'3,000원'" in findings[0]


def test_a_generic_research_institute_is_not_a_named_institution() -> None:
    features = FeatureModel(content={"headings": [Heading(level=1, text="기업부설 기술연구소 운영")]})
    html = "<html><body><p>한국대학교와 협력합니다</p></body></html>"
    assert find_corrupted_tokens(html, features) == []


def test_a_named_research_institute_is_still_an_institution() -> None:
    features = FeatureModel(content={"headings": [Heading(level=1, text="한국기술연구소 운영")]})
    html = "<html><body><p>서울대학교와 협력합니다</p></body></html>"
    assert any("한국기술연구소" in finding for finding in find_corrupted_tokens(html, features))


@pytest.mark.parametrize("source, rendered", [
    ("박상하준", "박상하윤"),
    ("남궁하은", "남궁하율"),
])
def test_a_changed_final_syllable_after_ha_in_a_name_is_still_reported(source, rendered) -> None:
    assert _finding(source, rendered)


@pytest.mark.parametrize("source, rendered", [
    ("02-555-0123 문의", "02-555-123 문의"),
    ("02-555-0123 문의", "2-555-0123 문의"),
    ("02-555-0123 문의", "02-555-0124 문의"),
])
def test_a_changed_phone_or_malformed_number_is_still_reported(source, rendered) -> None:
    assert _finding(source, rendered)


def test_dropping_a_trailing_country_or_office_character_is_still_reported() -> None:
    assert _finding("산업정책국", "산업정책")
    assert _finding("기술지원실", "기술지원")


def test_a_different_named_university_is_still_reported_even_with_a_generic_looking_prefix() -> None:
    features = FeatureModel(content={"headings": [Heading(level=1, text="서울대학교 협력")]})
    html = "<html><body><p>중앙대학교와 협력합니다</p></body></html>"
    assert any("서울대학교" in finding for finding in find_corrupted_tokens(html, features))


@pytest.mark.parametrize("candidate, source", [
    ("4.5", "4.05"),
    ("1.5", "1.05"),
    ("0.5", "0.05"),
])
def test_a_decimal_is_not_treated_as_a_reformatted_date(candidate, source) -> None:
    assert not _is_numeric_reformat(candidate, source)


def test_a_year_month_date_is_a_numeric_reformat() -> None:
    assert _is_numeric_reformat("2023.5", "2023.05")
