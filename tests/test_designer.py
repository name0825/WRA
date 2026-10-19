import json

import pytest

from wra.core.models import FeatureModel, Framework, FrameworkConfig
from wra.designer.planner import DesignPlanner
from wra.designer.recovery import DesignRecoveryError, RecoveryPolicy, build_recovery_prompt
from wra.validator.design import validate_design_spec


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, prompt, model, options=None, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, "options": options, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_design_spec() -> dict:
    return {
        "design": {
            "concept": "Editorial Minimal",
            "visual_direction": "Typography-focused editorial layout",
            "layout": "asymmetric",
            "color_system": {
                "background": "#fff",
                "surface": "#f5f5f5",
                "text": "#111",
                "muted_text": "#666",
                "primary": "#111",
                "accent": "#f50",
            },
            "typography": {
                "heading": "Inter",
                "body": "Inter",
                "scale": "large",
                "weight": "mixed",
            },
            "framework": {
                "css": {"name": "none", "reason": "not needed"},
                "js": {"name": "none", "reason": "not needed"},
            },
            "motion": {"level": "mid", "principles": ["subtle reveal"]},
        },
        "sections": [
            {
                "id": "featured",
                "purpose": "Present content",
                "layout": "grid",
                "components": [
                    {
                        "type": "card",
                        "purpose": "Display content",
                        "content_source": "content.text",
                    }
                ],
            }
        ],
        "responsive": {
            "strategy": "desktop-first",
            "breakpoints": [{"name": "mobile", "width": "480px"}],
            "behavior": ["single column"],
        },
        "interaction": {
            "principles": ["subtle transitions"],
            "components": [{"target": "card", "behavior": "hover"}],
        },
    }


def test_design_validator_rejects_duplicate_sections_and_bad_references():
    data = make_design_spec()
    data["sections"].append(data["sections"][0].copy())
    data["sections"][1]["id"] = "featured"
    data["sections"][0]["components"][0]["content_source"] = "content.unknown"
    data["interaction"]["components"][0]["target"] = "missing"

    from wra.designer.schema import DesignSpec

    result = validate_design_spec(DesignSpec.model_validate(data), FeatureModel(), FrameworkConfig())

    assert not result.valid
    assert "duplicate section id: featured" in result.errors
    assert "invalid content_source: content.unknown" in result.errors
    assert any(error.startswith("invalid interaction target: missing") for error in result.errors)


def test_design_validator_rejects_unconfigured_frameworks():
    from wra.designer.schema import DesignSpec

    data = make_design_spec()
    data["design"]["framework"]["css"]["name"] = "Tailwind"
    data["design"]["framework"]["js"]["name"] = "UnknownJS"

    result = validate_design_spec(DesignSpec.model_validate(data), FeatureModel(), FrameworkConfig())

    assert not result.valid
    assert any(error.startswith("CSS framework is not configured: Tailwind") for error in result.errors)
    assert any(error.startswith("JavaScript framework is not configured: UnknownJS") for error in result.errors)


CATALOG = FrameworkConfig(
    css=[Framework(name="TailwindCSS", cdn="https://example.com/tailwind.js", tag="script"),
         Framework(name="Bootstrap", cdn="https://example.com/bootstrap.css", tag="link")],
    js=[Framework(name="GSAP", cdn="https://example.com/gsap.js", tag="script")],
)


@pytest.mark.parametrize("written", ["TailwindCSS", "Tailwind", "tailwind", "Tailwind CSS", "tailwindcss"])
def test_a_framework_is_found_however_the_model_spells_it(written):
    """One catalogue entry, several spellings: an exact match rejected all but one."""
    assert CATALOG.find_css(written) is not None
    assert CATALOG.find_css(written).name == "TailwindCSS"


def test_a_name_that_is_not_in_the_catalogue_is_still_rejected():
    assert CATALOG.find_css("Bulma") is None
    assert CATALOG.find_js("Tailwind") is None, "normalizing must not move it between slots"


def test_a_spelling_variant_validates():
    from wra.designer.schema import DesignSpec

    data = make_design_spec()
    data["design"]["framework"]["css"]["name"] = "Tailwind"
    data["design"]["framework"]["js"]["name"] = "gsap"

    assert validate_design_spec(DesignSpec.model_validate(data), FeatureModel(), CATALOG).valid


def test_a_framework_in_the_wrong_slot_is_told_where_it_belongs():
    """The failure that killed two runs: TailwindCSS written into framework.js."""
    from wra.designer.schema import DesignSpec

    data = make_design_spec()
    data["design"]["framework"]["css"]["name"] = "none"
    data["design"]["framework"]["js"]["name"] = "Tailwind"

    result = validate_design_spec(DesignSpec.model_validate(data), FeatureModel(), CATALOG)

    assert not result.valid
    error = next(error for error in result.errors if error.startswith("JavaScript framework"))
    assert "belongs in the CSS slot" in error
    assert "GSAP" in error, "and what it could have used instead"


def test_design_validator_accepts_configured_frameworks():
    from wra.designer.schema import DesignSpec

    data = make_design_spec()
    data["design"]["framework"]["css"]["name"] = "TailwindCSS"
    data["design"]["framework"]["js"]["name"] = "GSAP"
    spec = DesignSpec.model_validate(data)
    frameworks = FrameworkConfig(
        css=[Framework(name="TailwindCSS", cdn="https://example.com/tailwind.js", tag="script")],
        js=[Framework(name="GSAP", cdn="https://example.com/gsap.js", tag="script")],
    )

    result = validate_design_spec(spec, FeatureModel(), frameworks)

    assert result.valid


def test_design_validator_rejects_empty_sections():
    from wra.designer.schema import DesignSpec

    data = make_design_spec()
    data["sections"] = []

    result = validate_design_spec(DesignSpec.model_validate(data), FeatureModel(), FrameworkConfig())

    assert not result.valid
    assert "sections must contain at least one section" in result.errors


def test_design_validator_rejects_section_without_components():
    from wra.designer.schema import DesignSpec

    data = make_design_spec()
    data["sections"][0]["components"] = []

    result = validate_design_spec(DesignSpec.model_validate(data), FeatureModel(), FrameworkConfig())

    assert not result.valid
    assert "section has no components: featured" in result.errors


def test_design_validator_rejects_missing_content_coverage():
    from wra.designer.schema import DesignSpec
    from wra.core.models import Heading

    features = FeatureModel(content={"headings": [Heading(level=1, text="Title")]})
    result = validate_design_spec(DesignSpec.model_validate(make_design_spec()), features, FrameworkConfig())

    assert not result.valid
    assert "missing content coverage: content.headings" in result.errors


def test_token_limit_increases_num_ctx_and_does_not_reuse_large_previous_response():
    valid = json.dumps(make_design_spec())
    truncated = "{" + ("x" * 12000)
    client = FakeLLMClient([truncated, valid])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=1, initial_num_ctx=4096, max_num_ctx=8192, growth_power=2.0),
    )

    result = planner.plan(FeatureModel())

    assert result.design.concept == "Editorial Minimal"
    # A schema, not the bare "json" grammar, which runs the designer away.
    assert isinstance(client.calls[0]["format"], dict)
    assert client.calls[1]["options"]["num_ctx"] == 8192
    assert "Failure type: token_limit" in client.calls[1]["prompt"]
    assert truncated not in client.calls[1]["prompt"]


def test_truncated_json_is_detected_as_token_limit():
    valid = json.dumps(make_design_spec())
    truncated = valid[:-24]
    client = FakeLLMClient([truncated, valid])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=1, initial_num_ctx=4096, max_num_ctx=8192, growth_power=2.0),
    )

    result = planner.plan(FeatureModel())

    assert result.design.concept == "Editorial Minimal"
    assert client.calls[1]["options"]["num_ctx"] == 8192
    assert "Failure type: token_limit" in client.calls[1]["prompt"]


def test_truncated_response_logs_warning_with_response_tail(caplog):
    valid = json.dumps(make_design_spec())
    truncated = "{" + ("x" * 12000)
    client = FakeLLMClient([truncated, valid])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=1, initial_num_ctx=4096, max_num_ctx=8192, growth_power=2.0),
    )

    with caplog.at_level("WARNING"):
        planner.plan(FeatureModel())

    assert truncated[-300:] in caplog.text
    assert "chars=12001" in caplog.text


def test_truncated_root_does_not_validate_inner_object_as_designspec():
    truncated = '{"design":{"concept":"Editorial Minimal","visual_direction":"x"}'

    with pytest.raises(ValueError, match="truncated"):
        DesignPlanner._parse_response(truncated)


def test_invalid_schema_includes_pydantic_field_details():
    data = make_design_spec()
    data["design"]["motion"]["level"] = "subtle"

    with pytest.raises(ValueError, match=r"design\.motion\.level"):
        DesignPlanner._parse_response(json.dumps(data))


def test_invalid_json_revises_previous_response():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient(["{broken", valid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))

    planner.plan(FeatureModel())

    assert "Previous response:\n{broken" in client.calls[1]["prompt"]
    assert "Failure type: json_structure" in client.calls[1]["prompt"]
    assert "DesignSpec required structure:" in client.calls[1]["prompt"]
    assert "content_source` must be a Feature Model dotted path" in client.calls[1]["prompt"]
    assert "interaction.components[].target` must exactly equal" in client.calls[1]["prompt"]


def test_invalid_design_recovery_reuses_previous_response():
    invalid = json.dumps({**make_design_spec(), "sections": []})
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient([invalid, valid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))

    planner.plan(FeatureModel())

    assert "Failure type: invalid_design" in client.calls[1]["prompt"]
    assert "sections must contain at least one section" in client.calls[1]["prompt"]
    assert invalid in client.calls[1]["prompt"]


@pytest.mark.parametrize("source", ["content.paragraphs[25]", "content.paragraphs.25"])
def test_out_of_range_index_coerced_on_last_attempt(source):
    out_of_range = json.dumps({
        **make_design_spec(),
        "sections": [
            {
                **make_design_spec()["sections"][0],
                "components": [
                    {
                        "type": "card",
                        "purpose": "Display content",
                        "content_source": source,
                    }
                ],
            }
        ],
    })
    client = FakeLLMClient([out_of_range, out_of_range])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"paragraphs": [f"p{i}" for i in range(25)]})

    spec = planner.plan(features)

    assert spec.sections[0].components[0].content_source == "content.paragraphs"


def test_missing_source_assets_prefix_coerced_on_last_attempt():
    """The failure that killed a run: the planner drops `source_assets.` reaching for the input's images."""
    from wra.core.models import AssetInfo

    base = make_design_spec()
    base["interaction"]["components"][0]["target"] = "figure"
    unprefixed = json.dumps({
        **base,
        "sections": [
            {
                **base["sections"][0],
                "components": [
                    {
                        "type": "figure",
                        "purpose": "Logo",
                        "content_source": "assets[0]",
                    }
                ],
            }
        ],
    })
    client = FakeLLMClient([unprefixed, unprefixed])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(source_assets={"assets": [AssetInfo(path="Logo.png", kind="image")]})

    spec = planner.plan(features)

    assert spec.sections[0].components[0].content_source == "source_assets.assets[0]"


@pytest.mark.parametrize("source", ["content.unknown[25]", "content.unknown.25"])
def test_unknown_content_source_not_coerced_still_raises(source):
    invalid = json.dumps({
        **make_design_spec(),
        "sections": [
            {
                **make_design_spec()["sections"][0],
                "components": [
                    {
                        "type": "card",
                        "purpose": "Display content",
                        "content_source": source,
                    }
                ],
            }
        ],
    })
    client = FakeLLMClient([invalid, invalid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))

    with pytest.raises(RuntimeError, match="failed after 1 retries"):
        planner.plan(FeatureModel())


@pytest.mark.parametrize("source", ["content.paragraphs[2..25]", "content.paragraphs[2:25]"])
def test_content_source_range_not_coerced_still_raises(source):
    invalid = json.dumps({
        **make_design_spec(),
        "sections": [
            {
                **make_design_spec()["sections"][0],
                "components": [
                    {
                        "type": "card",
                        "purpose": "Display content",
                        "content_source": source,
                    }
                ],
            }
        ],
    })
    client = FakeLLMClient([invalid, invalid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"paragraphs": [f"p{i}" for i in range(25)]})

    with pytest.raises(RuntimeError, match="failed after 1 retries"):
        planner.plan(features)


@pytest.mark.parametrize("source", ["content.paragraphs[24]", "content.paragraphs.24"])
def test_valid_spec_on_first_attempt_untouched_no_coercion(source):
    data = make_design_spec()
    data["sections"][0]["components"][0]["content_source"] = source
    valid = json.dumps(data)
    client = FakeLLMClient([valid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"paragraphs": [f"p{i}" for i in range(25)]})

    spec = planner.plan(features)

    assert spec.sections[0].components[0].content_source == source
    assert len(client.calls) == 1


def test_table_row_out_of_range_coerced_without_retry():
    data = make_design_spec()
    data["sections"][0]["components"][0]["content_source"] = "content.tables[4][1]"
    client = FakeLLMClient([json.dumps(data)])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"tables": [[["r0c0"]] for _ in range(5)]})

    spec = planner.plan(features)

    assert spec.sections[0].components[0].content_source == "content.tables[4]"
    assert len(client.calls) == 1


def test_table_error_with_other_error_still_retries():
    base = make_design_spec()
    data = {
        **base,
        "sections": [
            {**base["sections"][0], "components": [{"type": "card", "purpose": "Display content", "content_source": "content.tables[4][1]"}]},
            {"id": "second", "purpose": "Present content", "layout": "grid", "components": [{"type": "card", "purpose": "Display content", "content_source": "content.unknown"}]},
        ],
    }
    invalid = json.dumps(data)
    client = FakeLLMClient([invalid, invalid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"tables": [[["r0c0"]] for _ in range(5)]})

    with pytest.raises(RuntimeError, match="failed after 1 retries"):
        planner.plan(features)

    assert len(client.calls) == 2


def test_table_row_out_of_range_and_missing_table_coerced_together():
    base = make_design_spec()
    data = {
        **base,
        "sections": [
            {**base["sections"][0], "id": "featured", "components": [{"type": "card", "purpose": "Display content", "content_source": "content.tables[4][1]"}]},
            {**base["sections"][0], "id": "extra", "components": [{"type": "card", "purpose": "Display content", "content_source": "content.tables[5][0]"}]},
        ],
    }
    client = FakeLLMClient([json.dumps(data)])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"tables": [[["r0c0"]] for _ in range(5)]})

    spec = planner.plan(features)

    assert len(spec.sections) == 1
    assert spec.sections[0].id == "featured"
    assert spec.sections[0].components[0].content_source == "content.tables[4]"
    assert len(client.calls) == 1


def test_table_coercion_still_invalid_leaves_original_spec_for_last_resort(monkeypatch):
    """All errors are table-source errors, but coercing them empties every
    section, so the immediate revalidation still fails. The last-resort
    coercion must then see the model's original spec, not the emptied one."""
    base = make_design_spec()
    data = {
        **base,
        "sections": [
            {**base["sections"][0], "id": "featured", "components": [{"type": "card", "purpose": "Display content", "content_source": "content.tables[5][0]"}]},
        ],
    }
    invalid = json.dumps(data)
    client = FakeLLMClient([invalid, invalid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))
    features = FeatureModel(content={"tables": [[["r0c0"]] for _ in range(5)]})

    received = {}
    original = DesignPlanner._coerce_unresolvable_sources

    def spy(spec, feats):
        received["sections"] = len(spec.sections)
        received["source"] = spec.sections[0].components[0].content_source if spec.sections else None
        return original(spec, feats)

    monkeypatch.setattr(DesignPlanner, "_coerce_unresolvable_sources", staticmethod(spy))

    with pytest.raises(RuntimeError, match="failed after 1 retries"):
        planner.plan(features)

    assert received["sections"] == 1
    assert received["source"] == "content.tables[5][0]"


def test_plan_prompt_lists_existing_feature_model_paths():
    planner = DesignPlanner.__new__(DesignPlanner)
    prompt = planner._build_plan_prompt("SYSTEM", FeatureModel(), None, None)

    assert "Available Feature Model reference paths:" in prompt
    assert "`metadata`" in prompt
    assert "`content`" in prompt
    assert "`content.text`" in prompt
    assert "`navigation`" in prompt
    assert "`requirements.must_preserve`" in prompt
    assert "`source_assets.assets`" not in prompt
    assert "`source_assets`" not in prompt


def test_plan_prompt_lists_source_assets_only_when_non_empty():
    from wra.core.models import AssetInfo

    planner = DesignPlanner.__new__(DesignPlanner)
    features = FeatureModel(source_assets={"assets": [AssetInfo(path="Logo.png", kind="image")]})
    prompt = planner._build_plan_prompt("SYSTEM", features, None, None)

    assert "`source_assets.assets` (1 item; one index per reference, 0 to 0, written `source_assets.assets[0]`)" in prompt
    assert "`source_assets.styles`" not in prompt


def test_revision_prompt_preserves_core_context_and_bounds_previous_html():
    from wra.core.models import EvaluationDimension, EvaluationResult
    from wra.designer.schema import DesignSpec

    spec = DesignSpec.model_validate(make_design_spec())
    evaluation = EvaluationResult(
        design_completeness=EvaluationDimension(score=8),
        visual_hierarchy=EvaluationDimension(score=8),
        design_consistency=EvaluationDimension(score=8),
        content_preservation=EvaluationDimension(score=8),
        ux=EvaluationDimension(score=8),
        readability=EvaluationDimension(score=8),
        responsive_design=EvaluationDimension(score=8),
        originality=EvaluationDimension(score=8),
        html_quality=EvaluationDimension(score=8),
        overall_score=8,
    )
    html = "<html>" + ("x" * 20000) + "</html>"

    prompt = DesignPlanner._build_revision_prompt("SYSTEM", "REVISION {previous_design_spec} {previous_html} {evaluation_result}", FeatureModel(), spec, html, evaluation, None, FrameworkConfig())

    assert "Feature Model:" in prompt
    assert "Available Feature Model reference paths:" in prompt
    assert "Available configured frameworks:" in prompt
    assert "WRA: previous HTML excerpt truncated" in prompt
    assert len(prompt) < 18000


def test_parse_response_accepts_reasoning_and_markdown_wrappers():
    payload = json.dumps(make_design_spec())
    response = f"<think>Let me reason about the design first.</think>\n```json\n{payload}\n```"

    result = DesignPlanner._parse_response(response)

    assert result.design.concept == "Editorial Minimal"


def test_parse_response_accepts_prose_around_json():
    payload = json.dumps(make_design_spec())
    response = f"Here is the DesignSpec:\n{payload}\nDone."

    result = DesignPlanner._parse_response(response)

    assert result.design.concept == "Editorial Minimal"


def test_invalid_json_recovery_does_not_duplicate_original_prompt():
    prompt = "Feature Model: " + ("x" * 30000)
    previous = "{" + ("y" * 12000)
    recovery = build_recovery_prompt(prompt, previous, DesignRecoveryError.JSON_STRUCTURE, ["Designer LLM returned invalid JSON"], "")

    assert len(recovery) < len(prompt) + len(previous)
    assert prompt not in recovery
    assert previous in recovery


def test_token_limit_recovery_uses_original_prompt_without_previous_response():
    prompt = "Feature Model: " + ("x" * 30000)
    previous = "{" + ("y" * 12000)
    recovery = build_recovery_prompt(prompt, previous, DesignRecoveryError.TOKEN_LIMIT, ["Designer LLM response appears truncated"], "")

    assert prompt in recovery
    assert previous not in recovery
    assert "output token budget" in recovery


def test_unknown_error_regenerates_without_previous_response():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient([RuntimeError("connection reset"), valid])
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=1))

    planner.plan(FeatureModel())

    assert client.calls[1]["prompt"] == client.calls[0]["prompt"]


def test_recovery_stops_at_max_num_ctx_but_keeps_concision_guidance():
    valid = json.dumps(make_design_spec())
    client = FakeLLMClient([RuntimeError("context length exceeded"), RuntimeError("context length exceeded"), valid])
    planner = DesignPlanner(
        client,
        "designer-model",
        recovery_policy=RecoveryPolicy(max_retries=2, initial_num_ctx=8192, max_num_ctx=8192, growth_power=2.0),
    )

    planner.plan(FeatureModel())

    assert client.calls[1]["options"]["num_ctx"] == 8192
    assert client.calls[2]["options"]["num_ctx"] == 8192
    # The window cannot grow, so the retry must at least ask for a shorter
    # response instead of resending the prompt that overran unchanged.
    assert client.calls[0]["prompt"] in client.calls[2]["prompt"]
    assert "Failure type: token_limit" in client.calls[2]["prompt"]


def test_recovery_raises_after_retry_limit():
    client = FakeLLMClient([RuntimeError("connection reset")] * 3)
    planner = DesignPlanner(client, "designer-model", recovery_policy=RecoveryPolicy(max_retries=2))

    with pytest.raises(RuntimeError, match="failed after 2 retries"):
        planner.plan(FeatureModel())
