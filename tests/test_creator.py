"""Building a site from a brief, with no source page to read."""

import json
import logging
import re
from pathlib import Path

import pytest

from wra.cli import creating_config
from wra.core.inputs import InputError, discover
from wra.core.models import AssetInfo, BusinessInfo, VisionInsight, WRAConfig
from wra.core.preparation import InputPreparer
from wra.creator.creator import SITE_DRAFT_SCHEMA, CreatedPage, Creator, CreatorError, SiteDraft, _phone_tokens, _thinness, _weight, _without_unbacked_contacts, feature_model_for, seed_business
from wra.validator.claims import find_unsupported_promises
from support import sample_config_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT = PROJECT_ROOT / "prompts" / "creator" / "system.md"

BRIEF = "대구에서 25년 된 정밀 절삭공구 유통사. 엔드밀·드릴 취급. 빠른 납기가 강점. 문의 유도가 목표."

DRAFT = {
    "site_type": "business",
    "industry": "정밀 절삭공구 유통",
    "brand_voice": "직설적이고 신뢰감 있는",
    "business": {"company_name": "대성정밀", "services": ["엔드밀", "드릴"], "cta": ["견적 문의"]},
    "pages": [
        {
            "slug": "index", "title": "대성정밀", "entry": True, "purpose": "회사 소개와 문의 유도",
            "headings": ["25년의 절삭공구 유통", "빠른 납기"],
            "paragraphs": ["대구에서 25년간 정밀 절삭공구를 공급해 왔습니다."],
            "lists": [["엔드밀", "드릴"]], "buttons": ["견적 문의"], "images": ["assets/hero.png"],
        },
        {
            "slug": "products", "title": "취급 품목", "entry": False, "purpose": "품목 안내",
            "headings": ["취급 품목"], "paragraphs": ["엔드밀과 드릴을 상시 보유합니다."],
            "lists": [], "buttons": [], "images": [],
        },
    ],
}

ASSETS = [
    AssetInfo(path="assets/hero.png", kind="image", bytes=90000, width=1600, height=900,
              usable_as=["hero"], vision=VisionInsight(subject="절삭공구 클로즈업", role="product")),
    AssetInfo(path="assets/site.css", kind="css", bytes=500),
]


class ReplyClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []
        self.formats: list = []
        self.options: list = []

    def generate(self, prompt, model, options=None, images=None, *, think=None, format=None, timeout=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.options.append(options)
        self.formats.append(format)
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _creator(client, **kwargs) -> Creator:
    return Creator(llm_client=client, model="test", prompt_path=PROMPT, **kwargs)


def test_a_brief_becomes_a_site_of_pages() -> None:
    client = ReplyClient(json.dumps(DRAFT))
    draft = _creator(client).create(BRIEF, ASSETS)
    # A schema, not the bare "json" grammar.
    assert isinstance(client.formats[0], dict)
    assert [page.slug for page in draft.pages] == ["index", "products"]
    assert draft.business.company_name == "대성정밀"
    assert draft.pages[0].entry


def test_only_images_that_exist_are_offered_to_the_creator() -> None:
    client = ReplyClient(json.dumps(DRAFT))
    _creator(client).create(BRIEF, ASSETS)
    prompt = client.prompts[0]
    assert "assets/hero.png" in prompt
    assert "절삭공구 클로즈업" in prompt
    assert "assets/site.css" not in prompt, "a stylesheet is not an image the page can place"


def test_an_empty_brief_is_refused() -> None:
    with pytest.raises(CreatorError, match="empty"):
        _creator(ReplyClient("{}")).create("   ", ASSETS)


def test_a_page_referencing_an_image_that_was_never_supplied_is_rejected() -> None:
    invented = json.loads(json.dumps(DRAFT))
    invented["pages"][1]["images"] = ["assets/invented.jpg"]
    with pytest.raises(CreatorError, match="not supplied"):
        _creator(ReplyClient(json.dumps(invented)), max_retries=0).create(BRIEF, ASSETS)


def test_an_unfillable_page_is_rejected() -> None:
    """A page with a heading and nothing else should not exist."""
    thin = json.loads(json.dumps(DRAFT))
    thin["pages"][1]["paragraphs"] = []
    with pytest.raises(CreatorError, match="no paragraphs"):
        _creator(ReplyClient(json.dumps(thin)), max_retries=0).create(BRIEF, ASSETS)


def test_more_pages_than_allowed_are_rejected() -> None:
    with pytest.raises(CreatorError, match="at most 1"):
        _creator(ReplyClient(json.dumps(DRAFT)), max_pages=1, max_retries=0).create(BRIEF, ASSETS)


def test_retry_recovers_from_a_bad_first_response() -> None:
    thick = {**DRAFT, "pages": [_entry_page(5, 3)["pages"][0], DRAFT["pages"][1]]}
    client = ReplyClient("not json", json.dumps(thick))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)
    assert [page.slug for page in draft.pages] == ["index", "products"]
    assert client.calls == 2


def test_a_page_left_empty_by_removing_an_unbacked_contact_is_rejected_and_retried() -> None:
    entry = _entry_page(5, 3)["pages"][0]
    hollow = {**DRAFT, "pages": [entry, {**DRAFT["pages"][1], "paragraphs": ["문의 전화 02-999-0000"]}]}
    valid = {**DRAFT, "pages": [entry, DRAFT["pages"][1]]}
    client = ReplyClient(json.dumps(hollow), json.dumps(valid))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)
    assert client.calls == 2
    assert all(page.paragraphs for page in draft.pages)
    with pytest.raises(CreatorError, match="no paragraphs"):
        _creator(ReplyClient(json.dumps(hollow)), max_retries=0).create(BRIEF, ASSETS)


def test_the_feature_model_carries_the_created_content() -> None:
    draft = _creator(ReplyClient(json.dumps(DRAFT))).create(BRIEF, ASSETS)
    features = feature_model_for(draft, draft.pages[0], ASSETS)

    assert features.metadata.title == "대성정밀"
    assert [heading.text for heading in features.content.headings] == ["25년의 절삭공구 유통", "빠른 납기"]
    assert features.content.headings[0].level == 1
    assert features.content.paragraphs == ["대구에서 25년간 정밀 절삭공구를 공급해 왔습니다."]
    assert [image.src for image in features.media.images] == ["assets/hero.png"]
    assert features.media.images[0].alt == "절삭공구 클로즈업"
    assert features.site.industry == "정밀 절삭공구 유통"
    # What the creator decided is what the finished page must still say.
    assert "25년의 절삭공구 유통" in features.requirements.must_preserve
    assert "견적 문의" in features.requirements.must_preserve
    assert "절삭공구 클로즈업" in features.requirements.must_preserve


# --- discovery and wiring ---------------------------------------------------

def _brief_input(root: Path, *, with_html: bool = False) -> Path:
    root.mkdir(parents=True)
    (root / "brief.md").write_text(BRIEF, encoding="utf-8")
    if with_html:
        (root / "index.html").write_text("<html><body><h1>Existing</h1></body></html>", encoding="utf-8")
    return root


def test_a_folder_with_only_a_brief_asks_to_create(tmp_path: Path) -> None:
    bundle = discover(_brief_input(tmp_path / "input"))
    assert bundle.creates
    assert bundle.entry is None
    assert BRIEF in bundle.brief


def test_a_folder_with_html_still_redesigns(tmp_path: Path) -> None:
    bundle = discover(_brief_input(tmp_path / "input", with_html=True))
    assert not bundle.creates
    assert bundle.entry is not None
    # The brief is still read: it is context for a redesign, not a reason to ignore the page.
    assert BRIEF in bundle.brief


def test_an_empty_folder_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "input"
    empty.mkdir()
    with pytest.raises(InputError, match="Nothing to work from"):
        discover(empty)


def test_a_structured_brief_is_read_after_the_prose(tmp_path: Path) -> None:
    root = _brief_input(tmp_path / "input")
    (root / "brief.yaml").write_text("company: 대성정밀\nfounded: 1998", encoding="utf-8")
    brief = discover(root).brief
    assert brief.index("brief.md") < brief.index("brief.yaml")


def _config(freedom: str) -> WRAConfig:
    data = sample_config_data()
    data["content"] = {"freedom": freedom}
    data["input"] = {**data["input"], "vision": {**data["input"]["vision"], "enabled": False}}
    return WRAConfig.model_validate(data)


@pytest.mark.parametrize("freedom", ["low", "medium", "high", "ultra"])
def test_creating_forces_madmax(freedom: str) -> None:
    """Nothing below madmax is coherent when the source is being written here."""
    assert creating_config(_config(freedom)).content.freedom == "madmax"


def test_creating_leaves_madmax_alone() -> None:
    config = _config("madmax")
    assert creating_config(config) is config


class StubCreator:
    def __init__(self, draft) -> None:
        self.draft = draft
        self.brief = ""

    def create(self, brief, assets=None):
        self.brief = brief
        return self.draft


def test_preparation_builds_the_created_pages(tmp_path: Path) -> None:
    root = _brief_input(tmp_path / "input")
    (root / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 9000)
    bundle = discover(root)
    draft = _creator(ReplyClient(json.dumps(DRAFT))).create(BRIEF, ASSETS)
    creator = StubCreator(draft)
    run_dir = tmp_path / "run"

    pages = InputPreparer(config=_config("madmax"), analyzer=None, creator=creator).prepare(bundle, run_dir)

    assert [page.slug for page in pages] == ["index", "products"]
    assert pages[0].is_entry and not pages[1].is_entry
    assert all(page.created for page in pages)
    assert pages[1].content_focus == "품목 안내"
    assert BRIEF in creator.brief
    # The brief and each page's decisions are kept beside the run.
    assert json.loads((run_dir / "data" / "brief.json").read_text(encoding="utf-8"))["site_type"] == "business"
    assert (run_dir / "data" / "index" / "created.json").is_file()
    # There is no source page, so nothing pretends there was one.
    assert not (run_dir / "data" / "index" / "original.html").exists()
    assert pages[0].features.source_assets.impression is None


def test_preparation_refuses_to_create_without_a_creator(tmp_path: Path) -> None:
    bundle = discover(_brief_input(tmp_path / "input"))
    with pytest.raises(ValueError, match="no creator is configured"):
        InputPreparer(config=_config("madmax"), analyzer=None).prepare(bundle, tmp_path / "run")


# --- brief facts ------------------------------------------------------------

FACT_BRIEF = BRIEF + "\n- 사업자등록번호: 480-88-03967\n- 주소: 대구광역시 달성군 다사읍 세천로 128-1\n- 이메일: taaron7498@gmail.com\n- 전화: 053-242-7498\n- 팩스: 053-242-7499\n- 영업시간: 09:00 – 18:00\n"


def test_brief_facts_the_creator_dropped_are_seeded_into_the_entry_page_only() -> None:
    draft = _creator(ReplyClient(json.dumps(DRAFT))).create(FACT_BRIEF, ASSETS)
    entry = feature_model_for(draft, draft.pages[0], ASSETS)
    other = feature_model_for(draft, draft.pages[1], ASSETS)

    assert entry.business.phone == ["053-242-7498"]
    assert entry.business.email == ["taaron7498@gmail.com"]
    assert entry.business.address == "대구광역시 달성군 다사읍 세천로 128-1"
    assert "팩스: 053-242-7499" in entry.business.contact
    assert "사업자등록번호: 480-88-03967" in entry.business.business_information
    assert "영업시간: 09:00 – 18:00" in entry.business.business_information
    assert "053-242-7498" in entry.requirements.must_preserve
    assert entry.business.company_name == "대성정밀"
    assert not (other.business.phone or other.business.email or other.business.address or other.business.contact or other.business.business_information)
    assert other.business.services == ["엔드밀", "드릴"]


def test_a_value_the_creator_wrote_is_neither_replaced_nor_duplicated() -> None:
    draft_data = json.loads(json.dumps(DRAFT))
    draft_data["business"].update({"phone": ["0532427498"], "address": "대구광역시 달성군 다사읍 세천로 128-1 (본사)", "business_information": ["사업자등록번호: 480-88-03967"]})

    draft = _creator(ReplyClient(json.dumps(draft_data))).create(FACT_BRIEF, ASSETS)

    assert draft.business.phone == ["0532427498"]
    assert draft.business.address == "대구광역시 달성군 다사읍 세천로 128-1 (본사)"
    assert draft.business.business_information.count("사업자등록번호: 480-88-03967") == 1


def test_a_phone_the_brief_does_not_back_is_dropped_for_the_brief_phone() -> None:
    business = BusinessInfo(phone=["02-999-0000"], email=["fake@example.com"])

    seed_business(business, "서울 사무실\n- 전화: 02-123-4567\n- 이메일: hello@daeseong.co.kr")

    assert business.phone == ["02-123-4567"]
    assert business.email == ["hello@daeseong.co.kr"]


def test_a_phone_written_in_another_format_is_kept_once() -> None:
    business = BusinessInfo(phone=["021234567"])

    seed_business(business, "- 전화: 02-123-4567")

    assert business.phone == ["021234567"]


def test_an_invented_address_gives_way_to_the_brief_address() -> None:
    business = BusinessInfo(address="서울특별시 강남구 테헤란로 1")

    seed_business(business, "- 주소: 서울특별시 마포구 월드컵북로 21")

    assert business.address == "서울특별시 마포구 월드컵북로 21"


def test_a_brief_phone_found_in_contact_is_still_added_to_phone() -> None:
    business = BusinessInfo(phone=["02-999-0000"], contact=["전화 02-123-4567"])

    seed_business(business, "- 전화: 02-123-4567")

    assert business.phone == ["02-123-4567"]


@pytest.mark.parametrize("address", ["서울", "마포구", "21"])
def test_a_fragment_of_the_brief_address_does_not_hide_it(address: str) -> None:
    business = BusinessInfo(address=address)

    seed_business(business, "- 주소: 서울특별시 마포구 월드컵북로 21")

    assert business.address == "서울특별시 마포구 월드컵북로 21"


@pytest.mark.parametrize("phone", ["4567", "123-4567", "", "+82-2-999-4567", "02-123-4567 (내선 12)"])
def test_a_phone_that_is_not_the_brief_phone_is_replaced(phone: str) -> None:
    business = BusinessInfo(phone=[phone])

    seed_business(business, "- 전화: 02-123-4567")

    assert business.phone == ["02-123-4567"]


@pytest.mark.parametrize("phone", ["대표전화 02-123-4567", "02) 123-4567"])
def test_a_phone_with_the_brief_digits_stays_as_written(phone: str) -> None:
    business = BusinessInfo(phone=[phone])

    seed_business(business, "- 전화: 02-123-4567")

    assert business.phone == [phone]


def test_an_email_fragment_is_replaced_and_a_case_variant_is_kept() -> None:
    fragment = BusinessInfo(email=["o@daeseong.co.kr"])
    upper = BusinessInfo(email=["Hello@Daeseong.co.kr"])

    seed_business(fragment, "- 이메일: hello@daeseong.co.kr")
    seed_business(upper, "- 이메일: hello@daeseong.co.kr")

    assert fragment.email == ["hello@daeseong.co.kr"]
    assert upper.email == ["Hello@Daeseong.co.kr"]


def test_a_second_brief_phone_is_added_beside_the_creator_one() -> None:
    business = BusinessInfo(phone=["0212345678"])

    seed_business(business, "- 전화: 02-1234-5678, 010-1111-2222")

    assert business.phone == ["0212345678", "010-1111-2222"]


def test_without_brief_contact_facts_the_creator_ones_are_removed() -> None:
    business = BusinessInfo(phone=["02-999-0000"], email=["fake@example.com"], address="서울특별시 강남구 테헤란로 1")

    seed_business(business, "서울의 작은 카페입니다.")

    assert business.phone == []
    assert business.email == []
    assert business.address is None


def test_a_creator_address_the_brief_text_contains_is_kept() -> None:
    business = BusinessInfo(address="서울  마포구")

    seed_business(business, "서울 마포구에 있는 작은 카페입니다.")

    assert business.address == "서울  마포구"


def test_contact_lines_with_a_phone_or_email_the_brief_does_not_give_are_dropped() -> None:
    business = BusinessInfo(
        contact=["전화: 02-999-0000", "팩스: 02-555-0000", "카카오톡 @daeseong", "메일: fake@example.com"],
        business_information=["주차 가능", "대표자 김영희", "팩스: 02-777-0000"],
    )

    seed_business(business, "- 전화: 02-123-4567\n- 팩스: 02-555-9999")

    assert business.contact == ["카카오톡 @daeseong", "팩스: 02-555-9999"]
    assert business.business_information == ["주차 가능", "대표자 김영희", "팩스: 02-555-9999"]


def test_a_phone_inside_an_address_line_of_the_brief_is_kept() -> None:
    business = BusinessInfo(phone=["02-555-1234"])

    seed_business(business, "- 주소: 서울 마포구 월드컵북로 21 (Tel 02-555-1234)\n- 전화: 010-1111-2222")

    assert business.phone == ["02-555-1234", "010-1111-2222"]


def test_page_text_loses_a_phone_or_email_the_brief_does_not_give() -> None:
    draft_data = json.loads(json.dumps(DRAFT))
    page = draft_data["pages"][0]
    page["paragraphs"] = ["오늘도 정성껏 내립니다. 문의는 02-999-0000 으로 주세요.", "대표전화 02-123-4567 로 연락 주세요.", "2024-05-01 개업, 아메리카노 15,000원, 09:30-18:30 운영."]
    page["lists"] = [["메일 fake@example.com", "원두 직접 로스팅"]]

    draft = _creator(ReplyClient(json.dumps(draft_data))).create(BRIEF + "\n- 전화: 02-123-4567\n", ASSETS)

    assert draft.pages[0].paragraphs == ["오늘도 정성껏 내립니다.", "대표전화 02-123-4567 로 연락 주세요.", "2024-05-01 개업, 아메리카노 15,000원, 09:30-18:30 운영."]
    assert draft.pages[0].lists == [["원두 직접 로스팅"]]


def test_a_title_heading_and_button_lose_only_the_unbacked_number() -> None:
    draft_data = json.loads(json.dumps(DRAFT))
    page = draft_data["pages"][0]
    page.update({
        "title": "대성정밀 문의: 02-999-0000",
        "headings": ["전화 02-999-0000", "빠른 납기"],
        "paragraphs": ["문의는 02-999-0000 으로 주세요.", "대표전화 02-123-4567 로 연락 주세요."],
        "lists": [["02-999-0000", "드릴"]],
        "buttons": ["전화 02-999-0000"],
    })

    draft = _creator(ReplyClient(json.dumps(draft_data))).create(BRIEF + "\n- 전화: 02-123-4567\n", ASSETS)

    assert draft.pages[0].title == "대성정밀 문의"
    assert draft.pages[0].headings == ["전화", "빠른 납기"]
    assert draft.pages[0].paragraphs == ["대표전화 02-123-4567 로 연락 주세요."]
    assert draft.pages[0].lists == [["드릴"]]
    assert draft.pages[0].buttons == ["전화"]


@pytest.mark.parametrize("text", ["가격은 1500-2000원 입니다", "15000000원", "연혁 1600-1800년", "총 16001234명", "주문 0123456789"])
def test_a_number_that_is_not_a_phone_is_no_phone_token(text: str) -> None:
    assert _phone_tokens(text) == []


@pytest.mark.parametrize("text", ["02-123-4567", "010 1234 5678", "1588-1234", "02) 123-4567", "01012345678"])
def test_a_phone_shape_is_a_phone_token(text: str) -> None:
    assert _phone_tokens(f"문의 {text} 로") == [text]


def test_page_text_keeps_numbers_that_are_not_phones() -> None:
    draft_data = json.loads(json.dumps(DRAFT))
    paragraphs = ["가격은 1500-2000원 입니다", "연혁 1600-1800년", "총 16001234명 이용", "주문 0123456789 확인"]
    draft_data["pages"][0]["paragraphs"] = paragraphs

    draft = _creator(ReplyClient(json.dumps(draft_data))).create(BRIEF, ASSETS)

    assert draft.pages[0].paragraphs == paragraphs


def test_a_brief_phone_the_phone_pattern_misses_is_kept() -> None:
    draft_data = json.loads(json.dumps(DRAFT))
    draft_data["business"]["phone"] = ["0507-1234-5678"]
    draft_data["pages"][0]["paragraphs"] = ["안심번호 0507-1234-5678 로 연락 주세요."]

    draft = _creator(ReplyClient(json.dumps(draft_data))).create(BRIEF + "\n- 전화: 0507-1234-5678\n", ASSETS)

    assert draft.business.phone == ["0507-1234-5678"]
    assert draft.pages[0].paragraphs == ["안심번호 0507-1234-5678 로 연락 주세요."]


def _cleaned_page(brief: str, **fields) -> CreatedPage:
    draft_data = json.loads(json.dumps(DRAFT))
    draft_data["pages"][0].update(fields)
    return _without_unbacked_contacts(SiteDraft.model_validate(draft_data), brief).pages[0]


def test_a_mixed_line_loses_only_the_segments_with_an_unbacked_number() -> None:
    mixed = "영업시간 09:00-18:00 · 전화 02-123-4567 · 팩스 02-555-0000 · 주소 서울 마포구 월드컵북로 21"
    page = _cleaned_page("- 전화: 02-123-4567", paragraphs=[mixed, "팩스 02-555-0000 으로 보내 주세요."], lists=[["팩스 02-555-0000", mixed]])

    assert page.paragraphs == ["영업시간 09:00-18:00 · 전화 02-123-4567 · 주소 서울 마포구 월드컵북로 21"]
    assert page.lists == [["영업시간 09:00-18:00 · 전화 02-123-4567 · 주소 서울 마포구 월드컵북로 21"]]


def test_an_emptied_heading_or_button_is_dropped_and_not_counted() -> None:
    page = _cleaned_page("- 전화: 02-123-4567", title="02-999-0000", headings=["02-999-0000", "전화 02-999-0000", "빠른 납기"], buttons=["02-999-0000", "견적 문의"])

    assert page.headings == ["전화", "빠른 납기"]
    assert page.buttons == ["견적 문의"]
    assert page.title == ""
    draft_data = json.loads(json.dumps(DRAFT))
    draft_data["pages"][0]["headings"] = ["", "", "빠른 납기"]
    draft = SiteDraft.model_validate(draft_data)
    assert _weight(draft) == 3
    assert "1 heading(s)" in _thinness(draft.pages[0])


@pytest.mark.parametrize("text, expected", [("전화 (010-9999-8888) 문의", "전화 문의"), ("전화 [010-9999-8888] 문의", "전화 문의"), ("전화 （010-9999-8888） 문의", "전화 문의")])
def test_a_bracket_pair_wrapping_only_the_unbacked_number_goes_with_it(text: str, expected: str) -> None:
    assert _cleaned_page("- 전화: 02-123-4567", title=text).title == expected


@pytest.mark.parametrize("text", ["가격 1500-2000 사이", "1600-1800kcal", "1700-1900년"])
def test_a_range_of_numbers_is_no_representative_number(text: str) -> None:
    assert _phone_tokens(text) == []


@pytest.mark.parametrize("text", ["1588-1234", "1544-0000로 전화"])
def test_a_representative_number_is_still_a_phone_token(text: str) -> None:
    assert _phone_tokens(text) == [re.match(r"[\d-]+", text).group(0)]


def test_a_phone_is_not_backed_by_digits_of_unrelated_numbers_on_a_brief_line() -> None:
    business = BusinessInfo(phone=["010-2123-4567", "0507-1234-5678", "02-123-4567"])

    seed_business(business, "개업 2024-05-01 02-123-4567\n- 전화: 0507-1234-5678")

    assert business.phone == ["0507-1234-5678", "02-123-4567"]


@pytest.mark.parametrize("address, kept", [("서울특별시 마포구", True), ("서울 마포구 성산동", False), ("서울시 마포구", True), ("(서울 마포구)", False), ("(), .", False)])
def test_an_address_without_a_brief_address_is_kept_when_every_word_is_in_the_brief(address: str, kept: bool) -> None:
    business = BusinessInfo(address=address)

    seed_business(business, "서울 마포구에 있는 작은 카페입니다.")

    assert business.address == (address if kept else None)


def test_a_comma_separated_sentence_with_an_unbacked_number_is_dropped_whole() -> None:
    sentences = ["신선한 재료, 정직한 가격, 그리고 빠른 상담(02-999-0000)을 약속합니다.", "매장은 서울에 있으며, 예약은 02-999-0000으로 받습니다."]
    page = _cleaned_page("- 전화: 02-123-4567", paragraphs=[*sentences, "첫째, 품질입니다. 둘째, 상담은 02-999-0000, 셋째, 가격입니다.", "좋은 재료를 씁니다."])

    assert page.paragraphs == ["첫째, 품질입니다.", "좋은 재료를 씁니다."]


@pytest.mark.parametrize("brief_phone, written", [("02 - 123 - 4567", "02-123-4567"), ("031 - 123 - 4567", "031-123-4567"), ("+82 (0)10 1234 5678", "010-1234-5678"), ("0507 1234 5678", "0507-1234-5678")])
def test_a_brief_phone_written_with_spaces_still_backs_the_creator_phone(brief_phone: str, written: str) -> None:
    business = BusinessInfo(phone=[written])

    seed_business(business, f"- 전화: {brief_phone}")
    page = _cleaned_page(f"- 전화: {brief_phone}", paragraphs=[f"전화는 {written} 입니다."])

    assert written in business.phone
    assert page.paragraphs == [f"전화는 {written} 입니다."]


@pytest.mark.parametrize("address", ["서울 마포구, 독막로 12, 3층", "서울 마포구 독막로 12 3층 (본사)", "서울 마포구 독막로 12 3층."])
def test_an_address_with_punctuation_is_kept_when_its_words_are_in_the_brief(address: str) -> None:
    business = BusinessInfo(address=address)

    seed_business(business, "카페는 서울 마포구 독막로 12 3층에 있는 작은 가게입니다.")

    assert business.address == address


def test_an_address_number_is_not_backed_by_a_longer_number_in_the_brief() -> None:
    business = BusinessInfo(address="서울 마포구 21")

    seed_business(business, "2021년에 서울 마포구에 문을 열었습니다.")

    assert business.address is None


@pytest.mark.parametrize("address, brief", [("경상북도 경주시", "경북 경주시의 작은 가게"), ("전라북도 전주시", "전북 전주시의 작은 가게"), ("충청남도 천안시", "충남 천안시의 작은 가게")])
def test_a_province_name_is_read_in_its_short_form(address: str, brief: str) -> None:
    business = BusinessInfo(address=address)

    seed_business(business, brief)

    assert business.address == address


@pytest.mark.parametrize("brief_phone, written", [("02-123-4567", "+82 2 123 4567"), ("010-1234-5678", "+82-10-1234-5678")])
def test_an_international_rendering_of_the_brief_phone_is_kept(brief_phone: str, written: str) -> None:
    draft_data = json.loads(json.dumps(DRAFT))
    draft_data["business"]["phone"] = [written]
    draft_data["pages"][0]["paragraphs"] = [f"국제 전화는 {written} 입니다."]

    draft = _creator(ReplyClient(json.dumps(draft_data))).create(BRIEF + f"\n- 전화: {brief_phone}\n", ASSETS)

    assert draft.business.phone == [written]
    assert draft.pages[0].paragraphs == [f"국제 전화는 {written} 입니다."]


def test_the_creator_prompt_demands_verbatim_facts_and_bans_placeholders() -> None:
    prompt = " ".join(PROMPT.read_text(encoding="utf-8").split())

    assert "verbatim" in prompt and "dropping one is a failure" in prompt
    assert '"phone"' in prompt and '"business_information"' in prompt
    assert "[대표자명]" in prompt
    assert "alert('접수되었습니다')" in prompt
    business_schema = json.dumps(SITE_DRAFT_SCHEMA)
    assert all(f'"{field}"' in business_schema for field in ("phone", "email", "address", "contact", "business_information"))


def _entry_page(headings: int, lists: int) -> dict:
    page = dict(DRAFT["pages"][0])
    page["headings"] = [f"제목 {index}" for index in range(headings)]
    page["paragraphs"] = [f"문단 {index}" for index in range(headings)]
    page["lists"] = [[f"항목 {index}"] for index in range(lists)]
    return {**DRAFT, "pages": [page]}


def test_a_thin_entry_page_is_retried_with_its_counts() -> None:
    client = ReplyClient(json.dumps(_entry_page(3, 1)), json.dumps(_entry_page(6, 3)))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert len(draft.pages[0].headings) == 6
    assert "3 heading(s), 3 paragraph(s), 1 list(s)" in client.prompts[1]
    assert "at least 5 headings" in client.prompts[1]


def test_a_thick_entry_page_passes_on_the_first_try() -> None:
    client = ReplyClient(json.dumps(_entry_page(5, 3)))
    _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 1


def test_creator_retries_foreign_script_before_it_becomes_source_content() -> None:
    contaminated = _entry_page(5, 3)
    contaminated["pages"][0]["paragraphs"][0] = "올바른 제품과技术支持를 제공합니다."
    clean = _entry_page(5, 3)
    clean["pages"][0]["paragraphs"][0] = "올바른 제품과 기술 지원을 제공합니다."
    client = ReplyClient(json.dumps(contaminated), "not json", json.dumps(clean))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert "技术支持" in client.prompts[2]
    assert "draft.pages[0].paragraphs[0]" in client.prompts[2]
    assert "rewrite this text in Korean" in client.prompts[2]
    features = feature_model_for(draft, draft.pages[0], ASSETS)
    assert features.content.paragraphs[0] == "올바른 제품과 기술 지원을 제공합니다."


def _repair_reply(*texts: str) -> str:
    return json.dumps({"texts": [{"index": index, "text": text} for index, text in enumerate(texts)]}, ensure_ascii=False)


def test_creator_repairs_a_foreign_script_field_without_regenerating_the_draft() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = "올바른 제품과技术支持를 제공합니다."
    client = ReplyClient(json.dumps(dirty), _repair_reply("올바른 제품과 기술 지원을 제공합니다."))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert "技术支持" in client.prompts[1]
    assert client.formats[1] is not SITE_DRAFT_SCHEMA
    assert draft.pages[0].paragraphs[0] == "올바른 제품과 기술 지원을 제공합니다."
    assert draft.pages[0].paragraphs[1] == "문단 1"
    assert not re.search(r"[぀-ヿ㐀-䶿一-鿿]", draft.model_dump_json())


def test_creator_rejects_a_repair_that_still_contains_foreign_script() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = "올바른 제품과技术支持를 제공합니다."
    clean = _entry_page(5, 3)
    client = ReplyClient(json.dumps(dirty), _repair_reply("올바른 제품과 技术 지원"), json.dumps(clean))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert "draft.pages[0].paragraphs[0]" in client.prompts[2]
    assert draft.pages[0].paragraphs[0] == "문단 0"


@pytest.mark.parametrize("repair", ["not json", json.dumps({"texts": "oops"}), json.dumps({"other": []}), json.dumps({"texts": [{"index": 5, "text": "기술"}, {"index": 0, "text": ""}]})])
def test_creator_falls_back_to_retry_when_the_repair_call_is_unusable(repair: str) -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = "올바른 제품과技术支持를 제공합니다."
    client = ReplyClient(json.dumps(dirty), repair, json.dumps(_entry_page(5, 3)))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert draft.pages[0].paragraphs[0] == "문단 0"


def test_creator_falls_back_to_retry_when_the_repair_call_raises() -> None:
    class RaisingRepairClient(ReplyClient):
        def generate(self, prompt, model, options=None, images=None, *, think=None, format=None, timeout=None):
            if format is not SITE_DRAFT_SCHEMA:
                self.calls += 1
                raise TimeoutError("repair timed out")
            return super().generate(prompt, model, options, images, think=think, format=format, timeout=timeout)

    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = "올바른 제품과技术支持를 제공합니다."
    client = RaisingRepairClient(json.dumps(dirty), json.dumps(_entry_page(5, 3)))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert draft.pages[0].paragraphs[0] == "문단 0"
    assert len(client.prompts) == 2


LONG_DIRTY = "대구에서 25년간 정밀 절삭공구를 공급해 왔으며技术支持를 제공합니다 그리고 빠른 납기를 약속합니다"
LONG_CLEAN = "대구에서 25년간 정밀 절삭공구를 공급해 왔으며 기술 지원을 제공합니다 그리고 빠른 납기를 약속합니다"


def test_creator_applies_a_rewrite_that_passes_the_guard() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = LONG_DIRTY
    client = ReplyClient(json.dumps(dirty), _repair_reply(LONG_CLEAN))

    draft = _creator(client, max_retries=0).create(BRIEF, ASSETS)

    assert draft.pages[0].paragraphs[0] == LONG_CLEAN


@pytest.mark.parametrize("rewrite", ["제품", "가" * 200, LONG_CLEAN.replace("25", "30")], ids=["too-short", "too-long", "digits-changed"])
def test_creator_does_not_apply_a_rewrite_that_fails_the_guard(rewrite: str) -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = LONG_DIRTY
    client = ReplyClient(json.dumps(dirty), _repair_reply(rewrite), json.dumps(_entry_page(5, 3)))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert rewrite not in draft.pages[0].paragraphs
    assert draft.pages[0].paragraphs[0] == "문단 0"


@pytest.mark.parametrize(
    "items",
    [
        [{"index": "0", "text": LONG_CLEAN}],
        [{"index": 0, "text": 1}],
        [{"index": 7, "text": LONG_CLEAN}],
    ],
    ids=["string-index", "non-string-text", "unknown-index"],
)
def test_creator_ignores_malformed_repair_items(items: list) -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = LONG_DIRTY
    client = ReplyClient(json.dumps(dirty), json.dumps({"texts": items}), json.dumps(_entry_page(5, 3)))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert draft.pages[0].paragraphs[0] == "문단 0"


def test_creator_uses_the_first_rewrite_for_a_duplicate_index() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = LONG_DIRTY
    second = LONG_CLEAN.replace("기술 지원", "기술 상담")
    client = ReplyClient(json.dumps(dirty), json.dumps({"texts": [{"index": 0, "text": LONG_CLEAN}, {"index": 0, "text": second}]}, ensure_ascii=False))

    draft = _creator(client, max_retries=0).create(BRIEF, ASSETS)

    assert draft.pages[0].paragraphs[0] == LONG_CLEAN


def test_creator_rejects_a_draft_when_only_some_fields_were_repaired() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][1] = "빠른技术支持"
    dirty["pages"][0]["lists"][2][0] = "서비스サービス"
    client = ReplyClient(json.dumps(dirty), json.dumps({"texts": [{"index": 0, "text": "빠른 기술 지원"}]}, ensure_ascii=False), json.dumps(_entry_page(5, 3)))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert "draft.pages[0].lists[2][0]" in client.prompts[2]
    assert draft.pages[0].lists[2] == ["항목 2"]


def test_creator_writes_repairs_back_into_business_lists_and_buttons() -> None:
    dirty = _entry_page(5, 3)
    dirty["business"] = {**dirty["business"], "services": ["엔드밀技术", "드릴"]}
    dirty["pages"][0]["buttons"] = ["견적サービス"]
    client = ReplyClient(json.dumps(dirty), _repair_reply("엔드밀 기술", "견적 서비스"))

    draft = _creator(client, max_retries=0).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert draft.business.services == ["엔드밀 기술", "드릴"]
    assert draft.pages[0].buttons == ["견적 서비스"]


def test_creator_reports_the_script_rejection_when_the_repair_prompt_file_is_missing(tmp_path: Path) -> None:
    (tmp_path / "system.md").write_text(PROMPT.read_text(encoding="utf-8"), encoding="utf-8")
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = LONG_DIRTY
    client = ReplyClient(json.dumps(dirty))

    with pytest.raises(CreatorError, match="failed after 1 attempt.*foreign script"):
        Creator(llm_client=client, model="test", prompt_path=tmp_path / "system.md", max_retries=0).create(BRIEF, ASSETS)

    assert client.calls == 1


def test_creator_sends_the_context_size_with_the_repair_call() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = LONG_DIRTY
    client = ReplyClient(json.dumps(dirty), _repair_reply(LONG_CLEAN))

    _creator(client, max_retries=0).create(BRIEF, ASSETS)

    assert "num_ctx" in client.options[0]
    assert client.options[1] == client.options[0]


def test_a_clean_draft_makes_no_repair_call() -> None:
    client = ReplyClient(json.dumps(_entry_page(5, 3)))

    _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 1


def test_creator_repairs_a_paragraph_and_a_list_item_in_one_call() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][1] = "빠른技术支持"
    dirty["pages"][0]["lists"][2][0] = "서비스サービス"
    client = ReplyClient(json.dumps(dirty), _repair_reply("빠른 기술 지원", "서비스 안내"))

    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert draft.pages[0].paragraphs[1] == "빠른 기술 지원"
    assert draft.pages[0].lists[2] == ["서비스 안내"]
    assert draft.pages[0].lists[0] == ["항목 0"]


def test_creator_sends_only_real_foreign_script_for_repair() -> None:
    dirty = _entry_page(5, 3)
    dirty["pages"][0]["paragraphs"][0] = '문의 주소 "https://example.com/技术支持" 안내'
    dirty["pages"][0]["paragraphs"][1] = "공급 サービス 안내"
    dirty["pages"][0]["paragraphs"][2] = "엔드밀 技术 안내"
    client = ReplyClient(json.dumps(dirty), _repair_reply("공급 서비스 안내"))

    draft = _creator(client, max_retries=0).create(BRIEF + " 技术", ASSETS)

    assert client.calls == 2
    assert "공급 サービス 안내" in client.prompts[1]
    assert "example.com" not in client.prompts[1]
    assert "엔드밀 技术 안내" not in client.prompts[1]
    assert draft.pages[0].paragraphs[0] == '문의 주소 "https://example.com/技术支持" 안내'
    assert draft.pages[0].paragraphs[1] == "공급 서비스 안내"
    assert draft.pages[0].paragraphs[2] == "엔드밀 技术 안내"


def test_creator_exhausted_script_retries_do_not_keep_a_contaminated_thin_draft() -> None:
    data = _entry_page(3, 1)
    data["pages"][0]["paragraphs"][0] = "올바른 제품과技术支持를 제공합니다."
    client = ReplyClient(json.dumps(data))

    with pytest.raises(CreatorError, match="failed after 2 attempt.*foreign script"):
        _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 4


@pytest.mark.parametrize("field", ["title", "purpose", "headings", "paragraphs", "lists", "buttons"])
def test_creator_checks_every_page_text_field(field: str) -> None:
    data = json.loads(json.dumps(_entry_page(5, 3)))
    page = data["pages"][0]
    if field == "lists":
        page[field][0][0] = "지원サービス"
    elif isinstance(page[field], list):
        page[field][0] = "지원サービス"
    else:
        page[field] = "지원サービス"

    with pytest.raises(CreatorError, match="foreign script"):
        _creator(ReplyClient(json.dumps(data)), max_retries=0).create(BRIEF, ASSETS)


@pytest.mark.parametrize("field", ["company_name", "services", "products", "contact", "address", "email", "phone", "business_information", "cta"])
def test_creator_checks_business_text_recursively(field: str) -> None:
    data = json.loads(json.dumps(_entry_page(5, 3)))
    data["business"][field] = "지원サービス" if field in {"company_name", "address"} else ["지원サービス"]

    with pytest.raises(CreatorError, match=f"draft.business.{field}"):
        _creator(ReplyClient(json.dumps(data)), max_retries=0).create(BRIEF, ASSETS)


@pytest.mark.parametrize("field", ["site_type", "industry", "brand_voice"])
def test_creator_checks_site_text_fields(field: str) -> None:
    data = _entry_page(5, 3)
    data[field] = "지원サービス"

    with pytest.raises(CreatorError, match=f"draft.{field}"):
        _creator(ReplyClient(json.dumps(data)), max_retries=0).create(BRIEF, ASSETS)


def test_creator_allows_han_and_kana_actually_supplied_in_korean_brief() -> None:
    data = _entry_page(5, 3)
    data["pages"][0]["paragraphs"][0] = "技术支持 및 サービス 안내"
    client = ReplyClient(json.dumps(data))

    draft = _creator(client, max_retries=0).create(BRIEF + " 技术支持 サービス", ASSETS)

    assert draft.pages[0].paragraphs[0] == "技术支持 및 サービス 안내"
    assert client.calls == 1


def test_creator_does_not_restrict_scripts_for_non_korean_brief() -> None:
    data = _entry_page(5, 3)
    data["pages"][0]["paragraphs"][0] = "技术支持 サービス"

    draft = _creator(ReplyClient(json.dumps(data)), max_retries=0).create("Precision tools and technical support", ASSETS)

    assert draft.pages[0].paragraphs[0] == "技术支持 サービス"


@pytest.mark.parametrize("delimiter", ['"', "'", "<", ">"])
def test_creator_rejects_foreign_script_prose_after_a_url_delimiter(delimiter: str) -> None:
    data = _entry_page(5, 3)
    data["pages"][0]["paragraphs"][0] = f'문의 주소 "https://example.com/{delimiter}技术支持 안내'

    with pytest.raises(CreatorError, match="foreign script"):
        _creator(ReplyClient(json.dumps(data)), max_retries=0).create(BRIEF, ASSETS)


def test_creator_allows_a_quoted_unicode_url() -> None:
    data = _entry_page(5, 3)
    text = '문의 주소 "https://example.com/技术支持/サービス" 안내'
    data["pages"][0]["paragraphs"][0] = text
    client = ReplyClient(json.dumps(data))

    draft = _creator(client, max_retries=0).create(BRIEF, ASSETS)

    assert client.calls == 1
    assert draft.pages[0].paragraphs[0] == text


def test_creator_allows_latin_terms_and_non_visible_url_and_asset_identifiers() -> None:
    data = json.loads(json.dumps(_entry_page(5, 3)))
    data["pages"][0]["paragraphs"][0] = "제품과 CNC 및 CAD 지원 안내 https://example.com/技术支持"
    data["pages"][0]["images"] = ["assets/サービス.png"]
    data["business"]["contact"] = ["https://example.com/技术支持"]
    assets = [AssetInfo(path="assets/サービス.png", kind="image", bytes=1)]
    client = ReplyClient(json.dumps(data))

    draft = _creator(client, max_retries=0).create(BRIEF, assets)

    assert client.calls == 1
    assert "CNC 및 CAD" in draft.pages[0].paragraphs[0]
    assert draft.pages[0].images == ["assets/サービス.png"]


def test_a_bare_heading_makes_the_entry_page_thin() -> None:
    data = _entry_page(6, 3)
    data["pages"][0]["paragraphs"] = ["문단 하나"]
    data["pages"][0]["lists"] = [["a"], ["b"], ["c"]]
    client = ReplyClient(json.dumps(data), json.dumps(_entry_page(6, 3)))
    _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 2


def test_exhausted_retries_keep_the_thickest_draft() -> None:
    client = ReplyClient(json.dumps(_entry_page(3, 1)), json.dumps(_entry_page(4, 2)), json.dumps(_entry_page(2, 1)))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 3
    assert len(draft.pages[0].headings) == 4


def test_a_thin_draft_survives_a_later_invalid_response() -> None:
    client = ReplyClient(json.dumps(_entry_page(3, 1)), "not json")
    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert len(draft.pages[0].headings) == 3


def test_the_creator_prompt_states_the_entry_page_volume_target() -> None:
    prompt = " ".join(PROMPT.read_text(encoding="utf-8").split())

    assert "5 to 7 headed content blocks" in prompt and "no bare headings" in prompt
    assert 'exactly 3 items, each a list item written as "short title: one sentence"' in prompt
    assert "Still forbidden: invented numbers" in prompt


def _draft_with(button: str) -> dict:
    data = _entry_page(5, 3)
    data["pages"][0]["buttons"] = [button]
    return data


def test_an_offer_the_brief_never_made_is_retried_with_the_term_named() -> None:
    client = ReplyClient(json.dumps(_draft_with("견적은 무료입니다")), json.dumps(_draft_with("견적 요청")))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert "Remove these offers or terms: 무료" in client.prompts[1]
    assert draft.pages[0].buttons == ["견적 요청"]


def test_an_offer_the_brief_never_made_is_cut_and_accepted_on_the_first_try() -> None:
    client = ReplyClient(json.dumps(_draft_with("무료 견적 문의하기")))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 1
    assert draft.pages[0].buttons == ["견적 문의하기"]
    assert "무료" not in json.dumps(draft.model_dump(), ensure_ascii=False)


def test_a_cut_offer_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    client = ReplyClient(json.dumps(_draft_with("무료 견적 문의하기")))
    with caplog.at_level(logging.INFO, logger="wra.creator.creator"):
        _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert "Draft offers the brief never made" in caplog.text
    assert "found=['무료'] remaining=[]" in caplog.text


def test_an_unstrippable_offer_is_retried_even_when_another_offer_was_cut() -> None:
    data = _draft_with("할인 견적 요청")
    data["pages"][0]["paragraphs"][0] = "견적은 무료입니다"
    client = ReplyClient(json.dumps(data), json.dumps(_draft_with("견적 요청")))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    recovery = client.prompts[1][len(client.prompts[0]):]
    assert client.calls == 2
    assert "Remove these offers or terms: 무료" in recovery
    assert "할인" not in recovery
    assert draft.pages[0].buttons == ["견적 요청"]


def test_an_offer_only_in_business_cta_is_retried() -> None:
    data = _entry_page(5, 3)
    data["business"] = {**DRAFT["business"], "cta": ["견적은 무료입니다"]}
    clean = _entry_page(5, 3)
    clean["business"] = {**DRAFT["business"], "cta": ["견적 요청"]}
    client = ReplyClient(json.dumps(data), json.dumps(clean))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert draft.business.cta == ["견적 요청"]


def test_a_cut_business_cta_leaves_contacts_alone() -> None:
    data = _entry_page(5, 3)
    data["business"] = {**DRAFT["business"], "cta": ["무료 견적 요청"], "email": ["freeman@x.com"], "phone": ["02-1234-5678"]}
    client = ReplyClient(json.dumps(data))
    draft = _creator(client, max_retries=1).create(BRIEF + " 문의 freeman@x.com 02-1234-5678", ASSETS)

    assert client.calls == 1
    assert draft.business.cta == ["견적 요청"]
    assert draft.business.email == ["freeman@x.com"]
    assert draft.business.phone == ["02-1234-5678"]


def test_an_offer_the_brief_made_is_kept() -> None:
    client = ReplyClient(json.dumps(_draft_with("무료 견적 요청")))
    draft = _creator(client, max_retries=2).create(BRIEF + " 무료 견적 제공.", ASSETS)

    assert client.calls == 1
    assert draft.pages[0].buttons == ["무료 견적 요청"]


def test_exhausted_retries_cut_the_sentence_with_the_offer_that_stripping_could_not_remove() -> None:
    data = _draft_with("무료 견적 요청")
    data["pages"][0]["paragraphs"][0] = "견적은 무료입니다"
    data["pages"][0]["paragraphs"][1] = "빠른 납기를 약속합니다. 견적은 무료입니다.\n정밀 가공"
    client = ReplyClient(json.dumps(data))
    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert find_unsupported_promises(" ".join(draft.pages[0].paragraphs + draft.pages[0].buttons), BRIEF) == []
    assert draft.pages[0].buttons == ["견적 요청"]
    assert draft.pages[0].paragraphs[0] == "빠른 납기를 약속합니다.\n정밀 가공"
    assert draft.pages[0].paragraphs[1:] == data["pages"][0]["paragraphs"][2:]


def test_an_offer_cut_that_empties_a_page_keeps_the_offer_guidance_for_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from wra.creator import creator as creator_module

    hollow = _draft_with("무료 견적 요청")
    hollow["pages"].append({**DRAFT["pages"][1], "paragraphs": ["견적은 무료입니다"]})

    def empty_a_page(draft, brief):
        data = draft.model_dump()
        data["pages"][1]["paragraphs"] = []
        return SiteDraft.model_validate(data)

    monkeypatch.setattr(creator_module, "_without_promises", empty_a_page)
    client = ReplyClient(json.dumps(hollow), json.dumps(_draft_with("견적 요청")))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)

    assert client.calls == 2
    assert "Remove these offers or terms: 무료" in client.prompts[1]
    assert "The previous response was invalid" not in client.prompts[1]
    assert draft.pages[0].buttons == ["견적 요청"]


def test_a_fallback_draft_never_has_a_page_emptied_by_cutting_offer_sentences() -> None:
    data = _draft_with("견적 요청")
    data["pages"].append({**DRAFT["pages"][1], "paragraphs": ["견적은 무료입니다"]})
    client = ReplyClient(json.dumps(data))

    with pytest.raises(CreatorError, match="failed after 2 attempt.*무료"):
        _creator(client, max_retries=1).create(BRIEF, ASSETS)


def test_a_hit_that_merely_sits_inside_a_longer_word_is_not_an_offer() -> None:
    data = _draft_with("견적 요청")
    data["pages"][0]["paragraphs"][0] = "Freedom 할인율 안내"
    client = ReplyClient(json.dumps(data))
    draft = _creator(client, max_retries=1).create(BRIEF, ASSETS)

    assert client.calls == 1
    assert draft.pages[0].paragraphs[0] == "Freedom 할인율 안내"


def test_strings_that_were_nothing_but_the_offer_fail_the_creation() -> None:
    data = _draft_with("무료 견적 요청")
    page = data["pages"][0]
    page["title"], page["headings"], page["paragraphs"], page["lists"] = "무료", ["Free"], ["무료"], [["무료"]]
    client = ReplyClient(json.dumps(data))

    with pytest.raises(CreatorError, match="failed after 2 attempt.*무료"):
        _creator(client, max_retries=1).create(BRIEF, ASSETS)


def test_stripping_leaves_business_contacts_and_words_that_merely_contain_a_term() -> None:
    data = _draft_with("무료 견적 요청")
    data["business"] = {**DRAFT["business"], "email": ["freeman@x.com"], "cta": ["무료 견적 요청"]}
    data["pages"][0]["paragraphs"][0] = "Freedom 할인율 안내\n무료로 상담"
    client = ReplyClient(json.dumps(data))
    draft = _creator(client, max_retries=1).create(BRIEF + " 문의 freeman@x.com", ASSETS)

    assert draft.business.email == ["freeman@x.com"]
    assert draft.business.cta == ["견적 요청"]
    assert draft.pages[0].paragraphs[0] == "Freedom 할인율 안내 상담"
    assert draft.pages[0].paragraphs[1] == "문단 1"
