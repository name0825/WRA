"""Shapes real models returned instead of the DesignSpec contract.

Every case here was taken from a failing response in an actual run.
"""

import pytest

from wra.designer.planner import DesignPlanner
from wra.designer.schema import DesignSpec
from wra.llm.response import json_root_text

VALID = {
    "design": {
        "concept": "c", "visual_direction": "v", "layout": "l",
        "color_system": {"background": "#fff", "surface": "#eee", "text": "#111", "muted_text": "#666", "primary": "#00f", "accent": "#f00"},
        "typography": {"heading": "H", "body": "B", "scale": "1.25", "weight": "400"},
        "framework": {"css": {"name": "Bootstrap", "reason": "r"}, "js": {"name": "GSAP", "reason": "r"}},
        "motion": {"level": "low", "principles": ["subtle"]},
    },
    "sections": [{"id": "hero", "purpose": "p", "layout": "l", "components": [{"type": "heading", "purpose": "p", "content_source": "content.headings[0]"}]}],
    "responsive": {"strategy": "mobile-first", "breakpoints": [{"name": "tablet", "width": "768px"}], "behavior": ["stack"]},
    "interaction": {"principles": ["clear"], "components": [{"target": "heading", "behavior": "fade"}]},
}


def _spec(**overrides) -> DesignSpec:
    payload = {key: dict(value) if isinstance(value, dict) else value for key, value in VALID.items()}
    for key, value in overrides.items():
        payload[key] = value
    return DesignSpec.model_validate(payload)


def _responsive(breakpoints):
    return {"strategy": "mobile-first", "breakpoints": breakpoints, "behavior": ["stack"]}


def test_breakpoints_given_as_bare_widths() -> None:
    spec = _spec(responsive=_responsive(["480px", "768px", "1280px"]))
    assert [(item.name, item.width) for item in spec.responsive.breakpoints] == [
        ("mobile", "480px"), ("tablet", "768px"), ("desktop", "1280px"),
    ]


def test_breakpoints_given_as_one_name_to_width_map() -> None:
    spec = _spec(responsive=_responsive({"mobile": "0px", "tablet": "768px", "desktop": "1024px"}))
    assert [(item.name, item.width) for item in spec.responsive.breakpoints] == [
        ("mobile", "0px"), ("tablet", "768px"), ("desktop", "1024px"),
    ]


def test_breakpoints_given_as_single_pair_objects() -> None:
    spec = _spec(responsive=_responsive([{"mobile": "0px"}, {"tablet": "768px"}]))
    assert [(item.name, item.width) for item in spec.responsive.breakpoints] == [("mobile", "0px"), ("tablet", "768px")]


def test_breakpoint_widths_given_as_numbers() -> None:
    spec = _spec(responsive=_responsive([{"name": "tablet", "width": 768}]))
    assert spec.responsive.breakpoints[0].width == "768"


def test_breakpoints_that_are_already_correct_are_untouched() -> None:
    spec = _spec(responsive=_responsive([{"name": "tablet", "width": "641-1024"}]))
    assert (spec.responsive.breakpoints[0].name, spec.responsive.breakpoints[0].width) == ("tablet", "641-1024")


def test_type_scale_given_as_a_list_of_sizes() -> None:
    design = dict(VALID["design"], typography={"heading": "H", "body": "B", "scale": [12, 16, 24], "weight": "400"})
    assert _spec(design=design).design.typography.scale == "12, 16, 24"


def test_type_scale_given_as_a_map_of_roles() -> None:
    design = dict(VALID["design"], typography={"heading": "H", "body": "B", "scale": {"h1": "3rem", "body": "1rem"}, "weight": "400"})
    assert _spec(design=design).design.typography.scale == "h1: 3rem, body: 1rem"


def test_a_weight_string_is_left_alone() -> None:
    """Joining the whole typography object into `weight` made it a description, not a weight."""
    design = dict(VALID["design"], typography={"heading": "Inter", "body": "Noto", "scale": "1.25", "weight": "700/400"})
    assert _spec(design=design).design.typography.weight == "700/400"


def test_a_weight_map_is_flattened_to_its_own_entries() -> None:
    design = dict(VALID["design"], typography={"heading": "Inter", "body": "Noto", "scale": "1.25", "weight": {"heading": 700, "body": 400}})
    assert _spec(design=design).design.typography.weight == "heading: 700, body: 400"


def test_layout_given_as_a_list_of_notes() -> None:
    design = dict(VALID["design"], layout=["12-column grid", "sticky header"])
    assert _spec(design=design).design.layout == "12-column grid, sticky header"


def test_interaction_behavior_given_as_a_list() -> None:
    interaction = {"principles": ["clear"], "components": [{"target": "heading", "behavior": ["toggle language", "persist selection"]}]}
    assert _spec(interaction=interaction).interaction.components[0].behavior == "toggle language, persist selection"


def test_a_genuinely_incomplete_spec_is_still_rejected() -> None:
    """Normalizing shapes must not paper over content the model never sent."""
    design = dict(VALID["design"])
    design["color_system"] = {"background": "#fff", "surface": "#eee", "text": "#111", "primary": "#00f", "accent": "#f00"}
    with pytest.raises(Exception, match="muted_text"):
        _spec(design=design)


TRUNCATED_BODY = '{\n  "design": {\n    "concept": "' + "x" * 400 + '",\n    "sections": [\n      {\n        "type": "card",\n        "purpose": "some'


@pytest.mark.parametrize("wrapper", [
    "{body}",
    "```json\n{body}",
    "Here is the design spec you asked for.\n\n{body}",
    "<think>let me plan this out</think>\n{body}",
], ids=["bare", "markdown-fence", "prose-prefix", "reasoning-prefix"])
def test_a_truncated_response_is_recognised_through_its_wrapper(wrapper: str) -> None:
    """The wrapper is why truncation used to read as a merely invalid design."""
    assert DesignPlanner._has_unclosed_json_root(wrapper.format(body=TRUNCATED_BODY))


def test_a_complete_fenced_response_is_not_called_truncated() -> None:
    import json as json_module
    complete = "```json\n" + json_module.dumps(VALID) + "\n```"
    assert not DesignPlanner._has_unclosed_json_root(complete)


def test_json_root_text_unwraps_what_the_model_put_in_front() -> None:
    assert json_root_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert json_root_text('Sure thing.\n{"a": 1}') == '{"a": 1}'
    assert json_root_text('<think>hmm</think>\n{"a": 1}') == '{"a": 1}'
    assert json_root_text('{"a": 1}') == '{"a": 1}'
