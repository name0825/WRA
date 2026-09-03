from wra.core.models import FeatureModel, FrameworkConfig, Form, Heading, Image, Section
from wra.designer.schema import DesignSpec
from wra.validator.design import _validate_content_coverage, validate_design_spec
from tests.test_generator import make_spec


def test_design_validator_requires_structure_collections_coverage():
    features = FeatureModel(
        structure={
            "sections": [Section(tag="section", text="Source section")],
            "forms": [Form(action="/submit", method="post", fields=["name"])],
        }
    )
    errors: list[str] = []
    _validate_content_coverage(make_spec(), features, errors)

    assert "missing content coverage: structure.sections" in errors
    assert "missing content coverage: structure.forms" in errors


def test_design_validator_accepts_structure_collection_references():
    features = FeatureModel(
        structure={
            "sections": [Section(tag="section", text="Source section")],
            "forms": [Form(action="/submit", method="post", fields=["name"])],
        }
    )
    spec_data = make_spec().model_dump()
    spec_data["sections"][0]["components"] = [
        {"type": "section", "purpose": "Source structure", "content_source": "structure.sections"},
        {"type": "form", "purpose": "Source form", "content_source": "structure.forms"},
    ]
    spec = DesignSpec.model_validate(spec_data)

    result = validate_design_spec(spec, features, FrameworkConfig())

    assert result.valid


def test_design_validator_enforces_coverage_at_low_and_medium_freedom():
    features = FeatureModel(structure={"sections": [Section(tag="section", text="Source section")]})

    for freedom in ("low", "medium"):
        result = validate_design_spec(make_spec(), features, FrameworkConfig(), freedom)

        assert not result.valid
        assert "missing content coverage: structure.sections" in result.errors


def test_design_validator_skips_coverage_above_medium_freedom():
    features = FeatureModel(structure={"sections": [Section(tag="section", text="Source section")]})

    for freedom in ("high", "ultra", "madmax"):
        result = validate_design_spec(make_spec(), features, FrameworkConfig(), freedom)

        assert result.valid


def test_design_validator_accepts_dot_and_bracket_index_forms():
    features = FeatureModel(
        content={"headings": [Heading(level=1, text="Title")]},
        media={"images": [Image(src="a.png", alt="A"), Image(src="b.png", alt="B")]},
    )
    spec_data = make_spec().model_dump()
    spec_data["sections"][0]["components"] = [
        {"type": "heading", "purpose": "Title", "content_source": "content.headings.0"},
        {"type": "figure", "purpose": "Lead image", "content_source": "media.images[1]"},
    ]
    spec_data["interaction"]["components"] = []
    spec = DesignSpec.model_validate(spec_data)

    result = validate_design_spec(spec, features, FrameworkConfig(), content_freedom="madmax")

    assert result.valid, result.errors


def test_design_validator_rejects_out_of_range_dot_index():
    features = FeatureModel(media={"images": [Image(src="a.png", alt="A")]})
    spec_data = make_spec().model_dump()
    spec_data["sections"][0]["components"] = [
        {"type": "figure", "purpose": "Lead image", "content_source": "media.images.3"},
    ]
    spec_data["interaction"]["components"] = []
    spec = DesignSpec.model_validate(spec_data)

    result = validate_design_spec(spec, features, FrameworkConfig(), content_freedom="madmax")

    assert "invalid content_source: media.images.3" in result.errors


def test_design_validator_names_declared_types_on_invalid_interaction_target():
    features = FeatureModel(content={"headings": [Heading(level=1, text="Title")]})
    spec_data = make_spec().model_dump()
    spec_data["sections"][0]["components"] = [
        {"type": "heading", "purpose": "Title", "content_source": "content.headings"},
    ]
    spec_data["interaction"]["components"] = [{"target": "cta", "behavior": "hover lift"}]
    spec = DesignSpec.model_validate(spec_data)

    result = validate_design_spec(spec, features, FrameworkConfig(), content_freedom="madmax")

    assert "invalid interaction target: cta; declared component types are: heading" in result.errors
