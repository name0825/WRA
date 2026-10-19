import pytest

from wra.core.models import BusinessInfo, FeatureModel
from wra.validator.facts import extract_brief_facts, find_missing_business_facts

BRIEF = """# T-Aaron 홈페이지 제작

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

FULL_PAGE = """<html><body>
<h1>T-Aaron</h1>
<p>사업자등록번호 480-88-03967</p>
<p>대구광역시 달성군 다사읍 세천로 128-1</p>
<p>영업시간 09:00 – 18:00</p>
<a href="tel:0532427498">053 242 7498</a>
<p>팩스 053-242-7499</p>
<a href="mailto:Taaron7498@gmail.com">문의</a>
</body></html>"""


def _features() -> FeatureModel:
    business = BusinessInfo()
    facts = extract_brief_facts(BRIEF)
    business.phone, business.email, business.address = facts["phone"], facts["email"], facts["address"]
    business.contact, business.business_information = facts["contact"], facts["business_information"]
    return FeatureModel(business=business)


def test_the_brief_facts_are_extracted() -> None:
    facts = extract_brief_facts(BRIEF)

    assert facts["phone"] == ["053-242-7498"]
    assert facts["email"] == ["taaron7498@gmail.com"]
    assert facts["address"] == "대구광역시 달성군 다사읍 세천로 128-1"
    assert "팩스: 053-242-7499" in facts["contact"]
    assert "팩스: 053-242-7499" in facts["business_information"]
    assert "사업자등록번호: 480-88-03967" in facts["business_information"]
    assert "영업시간: 09:00 – 18:00" in facts["business_information"]


def test_the_registration_number_is_not_read_as_a_phone_number() -> None:
    facts = extract_brief_facts("- 사업자등록번호: 480-88-03967\n480-88-03967\n- 팩스: 053-242-7499")

    assert facts["phone"] == []


def test_an_unlabeled_phone_number_is_found() -> None:
    assert extract_brief_facts("문의는 010-1234-5678 로 주세요.")["phone"] == ["010-1234-5678"]


def test_a_page_showing_every_fact_reports_nothing() -> None:
    assert find_missing_business_facts(FULL_PAGE, _features()) == []


def test_phone_forms_are_matched_by_digits() -> None:
    features = FeatureModel(business=BusinessInfo(phone=["053-242-7498"]))

    for shown in ("tel:0532427498", "+82 53-242-7498", "053.242.7498", "<span>053</span>-<span>242</span>-<span>7498</span>"):
        assert find_missing_business_facts(f"<p>{shown}</p>", features) == []


def test_a_page_without_the_facts_reports_all_six() -> None:
    findings = find_missing_business_facts("<html><body><h1>T-Aaron</h1><p>산업자재 전문</p></body></html>", _features())

    assert len(findings) == 6
    assert all(finding.startswith("brief fact missing from page: ") for finding in findings)
    assert any("053-242-7498" in finding for finding in findings)
    assert any("053-242-7499" in finding for finding in findings)


def test_the_fax_number_does_not_stand_in_for_the_phone_number() -> None:
    page = FULL_PAGE.replace("tel:0532427498", "#").replace("053 242 7498", "")

    findings = find_missing_business_facts(page, _features())

    assert findings == ["brief fact missing from page: 전화 053-242-7498"]


def test_a_wrong_time_or_street_is_reported() -> None:
    page = FULL_PAGE.replace("18:00", "17:00").replace("세천로 128-1", "세천로 129")

    findings = find_missing_business_facts(page, _features())

    assert sorted(finding.split()[5] for finding in findings) == ["영업시간", "주소"]


def test_a_shortened_address_with_the_street_segment_matches() -> None:
    page = FULL_PAGE.replace("대구광역시 달성군 다사읍 세천로 128-1", "대구 달성군 세천로128-1")

    assert find_missing_business_facts(page, _features()) == []


def test_a_feature_model_without_business_facts_reports_nothing() -> None:
    assert find_missing_business_facts("<html></html>", FeatureModel()) == []


def _hours_features() -> FeatureModel:
    return FeatureModel(business=BusinessInfo(business_information=["영업시간: 09:00 – 18:00"]))


def test_korean_hour_forms_match_the_briefed_hours() -> None:
    for shown in ("오전 9시 ~ 오후 6시", "9시-18시", "09:00~18:00"):
        assert find_missing_business_facts(f"<p>{shown}</p>", _hours_features()) == []


def test_hours_absent_from_the_page_are_reported() -> None:
    findings = find_missing_business_facts("<p>오전 10시 ~ 오후 5시</p>", _hours_features())

    assert findings == ["brief fact missing from page: 영업시간 09:00 – 18:00"]


def test_brief_fact_findings_apply_to_created_pages_only(tmp_path) -> None:
    from wra.core.iteration import _brief_fact_findings
    from wra.core.preparation import PreparedPage

    features = FeatureModel(business=BusinessInfo(phone=["053-242-7498"]))
    made = dict(slug="index", relative="index.html", html="", features=features, run_root=tmp_path, data_dir=tmp_path, is_entry=True)

    assert _brief_fact_findings(PreparedPage(**made), "<p>no phone</p>") == []
    assert len(_brief_fact_findings(PreparedPage(**made, created=True), "<p>no phone</p>")) == 1


def _hours_page_findings(brief_hours: str, shown: str) -> list[str]:
    features = FeatureModel(business=BusinessInfo(business_information=[f"영업시간: {brief_hours}"]))
    return find_missing_business_facts(f"<p>{shown}</p>", features)


def test_hours_with_a_changed_minute_are_reported() -> None:
    assert _hours_page_findings("09:30–18:30", "오전 9시–오후 6시") != []
    assert _hours_page_findings("09:30–18:30", "09:00–18:30") != []


def test_hours_with_swapped_meridiem_are_reported() -> None:
    assert _hours_page_findings("09:00–18:00", "오후 9시–오전 6시") != []


def test_hours_in_other_formats_of_the_same_times_pass() -> None:
    for shown in ("오전 9시 30분–오후 6시 30분", "09:30–18:30", "9시 30분–18시 30분", "9:30 AM–6:30 PM"):
        assert _hours_page_findings("09:30–18:30", shown) == []
    for shown in ("9am–6pm", "오전 9시–18시", "9시–오후 6시"):
        assert _hours_page_findings("09:00–18:00", shown) == []
    assert _hours_page_findings("오전 9시–오후 6시 30분", "09:00–18:30") == []


@pytest.mark.parametrize(
    ("brief_hours", "shown"),
    [
        ("09:00–18:00", "9시~6시"),
        ("09:00–18:00", "평일 9시부터 6시까지"),
        ("09:00–18:00", "오전 9시 ~ 6시"),
        ("09:00–18:00", "아침 9시부터 저녁 6시까지"),
        ("09:00–18:00", "9:00 - 6:00"),
        ("13:00–22:00", "오후 1시~10시"),
        ("10:00-22:00", "오전 10시 ~ 밤 10시"),
        ("09:00", "9시"),
        ("09:00", "오전 9시"),
        ("09:00", "9:00"),
        ("11:00-21:00", "낮 11시 ~ 밤 9시"),
        ("18:00-02:00", "저녁 6시 ~ 밤 2시"),
        ("17:00-01:00", "오후 5시 ~ 밤 1시"),
    ],
)
def test_ordinary_korean_hour_notation_passes(brief_hours: str, shown: str) -> None:
    assert _hours_page_findings(brief_hours, shown) == []


@pytest.mark.parametrize(
    ("brief_hours", "shown"),
    [
        ("09:00–18:00", "오후 9시–오전 6시"),
        ("09:30–18:30", "오전 9시–오후 6시"),
        ("09:00–18:00", "09:00–18:30"),
        ("09:00", "오후 9시"),
        ("09:00", "21:00"),
        ("11:00-21:00", "오후 11시 ~ 오후 9시"),
        ("11:00-21:00", "23:00 ~ 21:00"),
    ],
)
def test_contradicting_hours_are_reported(brief_hours: str, shown: str) -> None:
    assert _hours_page_findings(brief_hours, shown) != []
