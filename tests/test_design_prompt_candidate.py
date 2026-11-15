import json
from pathlib import Path

import pytest

from wra.core.models import FeatureModel, Framework, FrameworkConfig
from wra.creator.creator import Creator, CreatorError
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import RecoveryPolicy as DesignRecoveryPolicy
from wra.designer.schema import DesignSpec
from wra.generator.generator import HTMLGenerator
from wra.generator.recovery import RecoveryPolicy as HTMLRecoveryPolicy
from test_designer import make_design_spec
from test_designer_revision_context import make_evaluation
from test_generator import make_spec

ROOT = Path(__file__).resolve().parent.parent / "benchmarks" / "design_prompt_comparison" / "candidate" / "prompts"


@pytest.fixture(autouse=True)
def forbid_browser_launch(monkeypatch):
    launches = []

    def unexpected_launch(*args, **kwargs):
        launches.append((args, kwargs))
        raise RuntimeError("Browser launch is not allowed in candidate prompt contract tests")

    monkeypatch.setattr("playwright.sync_api.sync_playwright", unexpected_launch)
    yield
    assert not launches, "Candidate prompt contract tests must not launch a browser"


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, prompt, model, options=None, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, "options": options, **kwargs})
        return self.responses.pop(0)


def draft(headings, paragraphs, lists=None, images=None):
    return {
        "site_type": "business",
        "industry": "products",
        "brand_voice": "plain",
        "business": {"company_name": "한빛", "services": [], "cta": []},
        "pages": [{"slug": "index", "title": "한빛", "entry": True, "purpose": "소개", "headings": headings, "paragraphs": paragraphs, "lists": lists or [], "buttons": [], "images": images or []}],
    }


def test_creator_candidate_short_brief_and_recovery_preserve_source_without_assets():
    page = draft(["한빛 소개"], ["한빛은 공구를 취급합니다."])
    client = Client("not json", json.dumps(page, ensure_ascii=False))
    creator = Creator(client, "test", prompt_path=ROOT / "creator/system.md", max_pages=1, max_retries=1)
    result = creator.create("한빛은 공구를 취급합니다.", [])
    assert len(client.calls) == 2
    assert result.pages[0].headings == ["한빛 소개"]
    assert result.pages[0].images == []
    for call in client.calls:
        prompt = call["prompt"]
        assert "한빛은 공구를 취급합니다." in prompt
        assert "available_images:\n[]" in prompt
        assert "at most 1 page(s)" in prompt
        assert "A short\nbrief may need only a few blocks" in prompt
        assert "Copy every checkable fact" in prompt
        assert isinstance(call["format"], dict)
    assert "previous response was invalid" in client.calls[1]["prompt"]


def test_creator_candidate_accepts_brief_sized_valid_page_without_padding():
    page = draft(["한빛 소개"], ["한빛은 공구를 취급합니다."])
    client = Client(json.dumps(page, ensure_ascii=False))
    result = Creator(client, "test", prompt_path=ROOT / "creator/system.md", max_pages=1, max_retries=1).create("한빛은 공구를 취급합니다.", [])
    assert len(client.calls) == 1
    assert result.pages[0].headings == ["한빛 소개"]
    assert result.pages[0].lists == []


def test_creator_candidate_multitopic_page_and_page_limit():
    page = draft(["절삭공구", "산업윤활유"], ["절삭공구를 취급합니다.", "산업윤활유를 취급합니다."], [["드릴", "엔드밀"], ["윤활유"]])
    client = Client(json.dumps(page, ensure_ascii=False))
    result = Creator(client, "test", prompt_path=ROOT / "creator/system.md", max_pages=1, max_retries=0).create("한빛은 드릴과 엔드밀, 산업윤활유를 취급합니다.")
    assert result.pages[0].lists == [["드릴", "엔드밀"], ["윤활유"]]
    assert len(result.pages) == 1
    page["pages"].append({**page["pages"][0], "entry": False, "slug": "products"})
    with pytest.raises(CreatorError, match="at most 1"):
        Creator(Client(json.dumps(page, ensure_ascii=False)), "test", prompt_path=ROOT / "creator/system.md", max_pages=1, max_retries=0).create("한빛은 드릴을 취급합니다.")


def test_creator_candidate_rejects_invented_asset():
    page = draft(["소개"], ["한빛은 공구를 취급합니다."], images=["assets/missing.png"])
    with pytest.raises(CreatorError, match="not supplied"):
        Creator(Client(json.dumps(page, ensure_ascii=False)), "test", prompt_path=ROOT / "creator/system.md", max_retries=0).create("한빛은 공구를 취급합니다.", [])


def test_planner_candidate_retry_preserves_schema_and_source_contract():
    spec = make_design_spec()
    client = Client("{broken", json.dumps(spec))
    features = FeatureModel(content={"text": ["기존 내용"]})
    planner = DesignPlanner(client, "test", FrameworkConfig(), prompt_path=ROOT / "designer/system.md", recovery_policy=DesignRecoveryPolicy(max_retries=1))
    result = planner.plan(features, design_constraints={"design_system": {"color_system": spec["design"]["color_system"]}})
    assert set(result.model_dump()) == {"design", "sections", "responsive", "interaction"}
    for call in client.calls:
        prompt = call["prompt"]
        assert "기존 내용" in prompt
        assert "content_source" in prompt
        assert '"design_system"' in prompt
        assert "Available configured frameworks" in prompt
    assert "visitor's main task" in client.calls[0]["prompt"]
    assert "fixed design system" in client.calls[0]["prompt"]
    assert "Failure type: json_structure" in client.calls[1]["prompt"]
    assert "DesignSpec required structure" in client.calls[1]["prompt"]


def test_candidate_revision_recovery_preserves_inherited_system_and_source():
    spec = make_design_spec()
    inherited = {
        "source_page": "index.html",
        **{key: spec["design"][key] for key in ("color_system", "typography", "framework", "motion")},
        "breakpoints": spec["responsive"]["breakpoints"],
        "instruction": "Copy these values exactly into the sibling page.",
    }
    features = FeatureModel(metadata={"title": "Original title"}, content={"text": ["Original body"]})
    evaluation = make_evaluation()
    evaluation.responsive_design.feedback = "Measured overflow at 390px"
    evaluation.improvement_suggestions = ["Contain the table at 390px"]
    client = Client("{broken", json.dumps(spec))
    planner = DesignPlanner(
        client, "test", FrameworkConfig(), prompt_path=ROOT / "designer/system.md",
        revision_prompt_path=ROOT / "designer/revision.md",
        recovery_policy=DesignRecoveryPolicy(max_retries=1),
    )
    result = planner.revise(
        features, DesignSpec.model_validate(spec), "<html>previous evidence</html>", evaluation,
        design_constraints={"design_system": inherited},
    )
    initial, recovery = (call["prompt"] for call in client.calls)
    assert "visitor's main task" in initial
    assert "Previous DesignSpec" in initial
    assert "previous evidence" in initial
    assert "Measured overflow at 390px" in initial
    assert "Contain the table at 390px" in initial
    for prompt in (initial, recovery):
        assert "Original title" in prompt
        assert "Original body" in prompt
        assert "`content.text`" in prompt
        assert "Available configured frameworks" in prompt
        assert "index.html" in prompt
        assert "Copy these values exactly into the sibling page." in prompt
        assert '"color_system"' in prompt
        assert '"typography"' in prompt
        assert '"framework"' in prompt
        assert '"motion"' in prompt
        assert '"breakpoints"' in prompt
    assert "Failure type: json_structure" in recovery
    assert "DesignSpec required structure" in recovery
    for key in ("color_system", "typography", "framework", "motion"):
        assert getattr(result.design, key).model_dump() == inherited[key]
    assert [item.model_dump() for item in result.responsive.breakpoints] == inherited["breakpoints"]


def test_generator_candidate_retry_preserves_source_placeholder_and_framework(monkeypatch):
    monkeypatch.setattr("wra.generator.js_repair.default_syntax_checker", lambda html: {})
    for name in ("repair_unknown_apply_utilities", "repair_oversized_header_logo", "repair_trapped_fixed", "repair_js_off_hidden", "repair_squeezed_text", "repair_horizontal_overflow", "repair_wide_tables", "repair_edge_hugging_text", "repair_protruding_skip_link", "repair_overridden_root_theme", "repair_low_contrast"):
        monkeypatch.setattr(f"wra.generator.generator.{name}", lambda html, asset_root=None: (html, []))
    complete = '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><main>기존 내용 WRAREF1</main></body></html>'
    client = Client("<div>broken</div>", complete)
    features = FeatureModel(content={"text": ["기존 내용 contact@example.com"]})
    frameworks = FrameworkConfig(css=[Framework(name="TailwindCSS", cdn="https://example.com/tailwind.js", tag="script")])
    generator = HTMLGenerator(client, "test", frameworks, prompt_path=ROOT / "generator/system.md", recovery_prompt_path=ROOT / "generator/recovery.md", recovery_policy=HTMLRecoveryPolicy(max_retries=1))
    result = generator.generate(features, make_spec(css_name="TailwindCSS"))
    assert "기존 내용" in result
    assert "contact@example.com" in result
    assert "WRAREF1" not in result
    assert "https://example.com/tailwind.js" in result
    for call in client.calls:
        prompt = call["prompt"]
        assert "기존 내용" in prompt
        assert "WRAREF1" in prompt
        assert "contact@example.com" not in prompt
        assert "TailwindCSS" in prompt
        assert "without invented loading or success states" in prompt
        assert "Do not invent external assets or URLs" in prompt
    assert len(client.calls) == 2

