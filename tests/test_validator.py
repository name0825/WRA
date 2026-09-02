from wra.core.models import FeatureModel, FrameworkConfig, Form, Section
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
