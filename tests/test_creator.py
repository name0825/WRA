"""Building a site from a brief, with no source page to read."""

import json
from pathlib import Path

import pytest
import yaml

from wra.cli import creating_config
from wra.core.inputs import InputError, discover
from wra.core.models import AssetInfo, VisionInsight, WRAConfig
from wra.core.preparation import InputPreparer
from wra.creator.creator import Creator, CreatorError, feature_model_for

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CONFIG = PROJECT_ROOT / "config" / "config.yaml.sample"
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

    def generate(self, prompt, model, options=None, images=None, *, think=None, timeout=None):
        self.calls += 1
        self.prompts.append(prompt)
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _creator(client, **kwargs) -> Creator:
    return Creator(llm_client=client, model="test", prompt_path=PROMPT, **kwargs)


def test_a_brief_becomes_a_site_of_pages() -> None:
    draft = _creator(ReplyClient(json.dumps(DRAFT))).create(BRIEF, ASSETS)
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
    client = ReplyClient("not json", json.dumps(DRAFT))
    draft = _creator(client, max_retries=2).create(BRIEF, ASSETS)
    assert [page.slug for page in draft.pages] == ["index", "products"]
    assert client.calls == 2


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
    data = yaml.safe_load(SAMPLE_CONFIG.read_text(encoding="utf-8"))
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
