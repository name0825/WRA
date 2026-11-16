from wra.core.models import FeatureModel
from wra.validator.forms import find_invented_forms


def test_extra_form_is_reported() -> None:
    findings = find_invented_forms("<body><FORM action='/x'><input name='a'></FORM></body>", FeatureModel())

    assert len(findings) == 1
    assert "1 <form>" in findings[0]
    assert "had 0" in findings[0]


def test_source_forms_are_not_reported() -> None:
    features = FeatureModel(structure={"forms": [{"fields": ["email"]}]})

    assert find_invented_forms("<form><input name='email'></form>", features) == []
    assert find_invented_forms("<p>no form</p>", FeatureModel()) == []


def test_invented_form_findings_skip_created_pages(tmp_path) -> None:
    from wra.core.iteration import _invented_form_findings
    from wra.core.preparation import PreparedPage

    made = dict(slug="index", relative="index.html", html="", features=FeatureModel(), run_root=tmp_path, data_dir=tmp_path, is_entry=True)

    assert _invented_form_findings(PreparedPage(**made, created=True), "<form></form>") == []
    assert len(_invented_form_findings(PreparedPage(**made), "<form></form>")) == 1
