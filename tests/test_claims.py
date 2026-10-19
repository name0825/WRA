import pytest

from wra.core.models import Content, FeatureModel
from wra.validator.claims import find_meta_notes, find_unsupported_claims, find_unsupported_places, find_unsupported_promises, strip_promises

BRIEF = """# T-Aaron 홈페이지 제작
- 회사명: T-Aaron ((주)티아론)
- 업종: 산업자재 · 공구 유통
- 주소: 대구광역시 달성군 다사읍 세천로 128-1
- 취급 품목: 절삭공구(인서트, 드릴, 앤드밀, 홀더), 산업윤활유, 원자재 수입
"""

REAL_BRIEF = """# T-Aaron 홈페이지 제작

우리 회사의 홈페이지를 제작해줘. 단일 랜딩페이지로 일단 만들어줘.
깔끔하고 전문적인 분위로 만들줘.

## 회사 정보

- 회사명: T-Aaron ((주)티아론)
- 업종: 산업자재 · 공구 유통
- 사업자등록번호: 480-88-03967
- 주소: 대구광역시 달성군 다사읍 세천로 128-1
- 이메일: taaron7498@gmail.com
- 전화: 053-242-7498
- 팩스: 053-242-7499
- 영업시간: 09:00 – 18:00
- 취급 품목: 절삭공구(인서트, 드릴, 앤드밀, 홀더), 산업윤활유, 원자재 수입
"""

PAGE ="""<html><body>
<a href="#contact" class="btn">무료 견적 요청</a>
<p>대구광역시 달성군 다사읍 세천로 128-1</p>
<p>Daejeon/Daegu (Based on text)</p>
<p>인서트 (Insert)</p><p>신속한 납품을 보장합니다.</p>
<script>var free = "Seoul";</script>
</body></html>"""


def test_a_place_the_source_never_mentions_is_flagged() -> None:
    assert find_unsupported_places("Daejeon", "대구에 있는 회사") == ["place name not in the source: Daejeon"]


def test_a_place_the_source_mentions_is_not_flagged_in_either_script() -> None:
    assert find_unsupported_places("대구광역시 달성군", "대구광역시 달성군") == []
    assert find_unsupported_places("Daegu office", "대구광역시") == []
    assert find_unsupported_places("대구 본사", "Daegu") == []


@pytest.mark.parametrize("text", ["세종대왕 연구", "광주 지역 말고 경기 결과", "경기 시작", "Sejongro street", "Seoulful"])
def test_ambiguous_short_forms_and_embedded_romanizations_do_not_count(text: str) -> None:
    assert find_unsupported_places(text, "대구") == []


@pytest.mark.parametrize("text", ["경기도 성남", "세종시 청사", "세종특별자치시", "광주시 오포", "광주광역시", "서울 지점", "Gwangju branch"])
def test_ambiguous_short_forms_count_with_an_administrative_suffix(text: str) -> None:
    assert len(find_unsupported_places(text, "대구")) == 1


@pytest.mark.parametrize("text", ["부산물 처리", "부산하게 움직이는", "대구경 배관", "대전제 아래", "서울대 출신", "info@busan-steel.com", "https://busan-steel.com/서울", "www.seoul-tools.kr"])
def test_places_inside_longer_words_emails_and_urls_do_not_count(text: str) -> None:
    assert find_unsupported_places(text, "울산") == []


@pytest.mark.parametrize("text", ["인천공항 인근", "서울에서 출발", "부산광역시", "제주도", "서울의 본사", "대구권 물류"])
def test_places_followed_by_a_suffix_or_particle_count(text: str) -> None:
    assert len(find_unsupported_places(text, "울산")) == 1


@pytest.mark.parametrize("source, text", [("광주 북구 1번지", "Gwangju"), ("광주 북구", "광주광역시"), ("세종 조치원", "Sejong"), ("경기 성남", "경기도 성남")])
def test_a_short_form_in_the_source_supports_the_full_name_on_the_page(source: str, text: str) -> None:
    assert find_unsupported_places(text, source) == []


@pytest.mark.parametrize("text", ["Daejeon/Daegu (Based on text)", "(based on brief)", "(placeholder)", "(Assumed)", "(TBD)", "(source: brief)", "Lorem ipsum dolor", "대표자: [대표자명]", "[주소]", "[회사명]"])
def test_model_notes_are_flagged(text: str) -> None:
    assert find_meta_notes(text)


@pytest.mark.parametrize("text", ["[제품 설명]", "[제품설명]", "[설명]", "[명절 휴무 안내]", "[주소] 대구광역시 달성군", "[회사명] 소개"])
def test_bracket_labels_and_headings_are_not_placeholders(text: str) -> None:
    assert find_meta_notes(text) == []


@pytest.mark.parametrize("text", ["인서트 (Insert)", "절삭공구 (Cutting tools)", "[NEW] 신제품", "[2024] 공지", "(주)티아론"])
def test_legitimate_parentheticals_are_not_notes(text: str) -> None:
    assert find_meta_notes(text) == []


def test_a_promise_is_flagged_only_when_the_source_lacks_it() -> None:
    assert find_unsupported_promises("무료 견적 요청", "견적 문의") == ["무료"]
    assert find_unsupported_promises("무료 견적 요청", "무료 견적") == []
    assert find_unsupported_promises("Free shipping, 100% off", "shipping") == ["100%", "Free"]
    assert find_unsupported_promises("특별 할인 안내", "") == ["할인"]
    assert find_unsupported_promises("Freedom 할인율 안내", "") == []


@pytest.mark.parametrize("text", ["신속한 납품을 보장합니다", "품질을 보증하고 약속합니다", "carefree freedom", "No. 12", "hands-free", "lead-free solder", "Free-cutting steel", "no 1 way", "No 1 way"])
def test_qualitative_prose_is_never_a_promise(text: str) -> None:
    assert find_unsupported_promises(text, "") == []


@pytest.mark.parametrize("text, term", [("Free", "Free"), ("free consultation", "free"), ("We are No.1 in tools", "No.1")])
def test_standalone_english_promises_are_flagged(text: str, term: str) -> None:
    assert find_unsupported_promises(text, "") == [term]


def test_stripping_a_promise_closes_the_gap() -> None:
    assert strip_promises("무료 견적 요청", ["무료"]) == "견적 요청"
    assert strip_promises("무료로 상담", ["무료"]) == "상담"
    assert strip_promises("무료", ["무료"]) == ""


def test_stripping_only_removes_the_term_where_it_stands_as_a_word() -> None:
    assert strip_promises("Freedom 할인율\n안내", ["Free", "할인"]) == "Freedom 할인율\n안내"
    assert strip_promises("Free shipping", ["Free"]) == "shipping"
    assert strip_promises("a  b\n무료 c", ["무료"]) == "a b c"
    assert strip_promises("a  b\nc", ["무료"]) == "a  b\nc"


def test_the_real_brief_and_measured_page_snippet_flag_exactly_the_invented_place_note_and_promise() -> None:
    features = FeatureModel(content=Content(text=[REAL_BRIEF]))

    assert find_unsupported_claims(PAGE, features) == [
        "place name not in the source: Daejeon",
        "model note left in the copy: (Based on text)",
        "commercial promise not in the source: 무료",
    ]


def test_the_measured_page_flags_exactly_the_invented_place_note_and_promise() -> None:
    features = FeatureModel(content=Content(text=[BRIEF]))

    assert find_unsupported_claims(PAGE, features) == [
        "place name not in the source: Daejeon",
        "model note left in the copy: (Based on text)",
        "commercial promise not in the source: 무료",
    ]


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("서울", "서울특별시청 안내"),
        ("부산광역시", "부산 광역시 해운대구"),
        ("대구", "대구광역 시청 앞"),
    ],
)
def test_a_place_the_source_has_with_other_spacing_or_attached_text_is_not_flagged(text: str, source: str) -> None:
    assert find_unsupported_places(text, source) == []


def test_a_place_absent_from_a_respaced_source_is_still_flagged() -> None:
    assert find_unsupported_places("서울", "부산 광역시청") == ["place name not in the source: 서울"]


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("부산 지사", "부산물 유통"),
        ("전남 지역", "완전 남자 의류"),
        ("대전", "대전제 조건"),
        ("경기도 지사", "경기장 안내"),
        ("세종시", "세종대왕 기념관"),
        ("전남 지역", "완전 남은 시간"),
        ("경남 지역", "환경 남은 시간"),
        ("제주", "숙제 주는 방법"),
        ("대전", "최대 전에 확인"),
        ("부산", "기부 산이 많다"),
    ],
)
def test_a_place_is_not_supported_by_an_unrelated_word_that_merely_contains_it(text: str, source: str) -> None:
    assert len(find_unsupported_places(text, source)) == 1


@pytest.mark.parametrize("text", ["Feel free to contact us", "feel free to call"])
def test_feel_free_is_not_a_promise(text: str) -> None:
    assert find_unsupported_promises(text, "") == []
    assert strip_promises(text, ["free"]) == text


@pytest.mark.parametrize("text", ["Free to join", "free to download"])
def test_free_to_do_something_is_still_a_promise(text: str) -> None:
    assert len(find_unsupported_promises(text, "")) == 1


def test_a_standalone_free_is_still_a_promise() -> None:
    assert find_unsupported_promises("Free shipping, feel free to ask", "") == ["Free"]
