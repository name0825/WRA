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

    error = next(error for error in result.errors if error.startswith("invalid content_source: media.images.3"))
    assert "highest index is 0" in error, "the limit has to travel with the error, or retries guess"


def _spec_sourcing(path: str):
    spec_data = make_spec().model_dump()
    spec_data["sections"][0]["components"] = [
        {"type": "figure", "purpose": "Lead image", "content_source": path},
    ]
    spec_data["interaction"]["components"] = []
    return DesignSpec.model_validate(spec_data)


def test_an_out_of_range_index_reports_the_real_size():
    """The failure that killed two runs: [8] on a collection of 7."""
    features = FeatureModel(media={"images": [Image(src=f"{n}.png", alt=str(n)) for n in range(7)]})

    result = validate_design_spec(_spec_sourcing("media.images[8]"), features, FrameworkConfig(), content_freedom="madmax")

    assert not result.valid
    assert any("media.images holds 7 items, so its highest index is 6" in error for error in result.errors)


def test_an_index_on_an_empty_collection_says_it_is_empty():
    result = validate_design_spec(_spec_sourcing("media.images[0]"), FeatureModel(), FrameworkConfig(), content_freedom="madmax")

    assert any("media.images is empty and has no valid index" in error for error in result.errors)


def test_an_index_on_an_empty_collection_points_at_source_assets_when_present():
    """The failure that killed a run: no `<img>` in the page, but Logo.png sits in source_assets.assets."""
    from wra.core.models import AssetInfo

    features = FeatureModel(source_assets={"assets": [AssetInfo(path="Logo.png", kind="image")]})

    result = validate_design_spec(_spec_sourcing("media.images[0]"), features, FrameworkConfig(), content_freedom="madmax")

    assert any("the input's image files are at `source_assets.assets[0]`..`[0]`" in error for error in result.errors)


def test_an_index_under_an_unknown_collection_says_so():
    result = validate_design_spec(_spec_sourcing("content.nope[1]"), FeatureModel(), FrameworkConfig(), content_freedom="madmax")

    assert any("content.nope does not resolve either" in error for error in result.errors)


def test_a_path_without_an_index_gets_no_index_hint():
    result = validate_design_spec(_spec_sourcing("content.nope"), FeatureModel(), FrameworkConfig(), content_freedom="madmax")

    assert "invalid content_source: content.nope" in result.errors


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
