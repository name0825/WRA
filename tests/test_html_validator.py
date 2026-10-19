from wra.core.models import FeatureModel
from wra.validator.html import validate_html


def test_validate_html_accepts_valid_document() -> None:
    features = FeatureModel()
    html = """<!DOCTYPE html>
<html><head><title>Test</title></head>
<body><main><h1>Hello</h1></main></body></html>"""

    result = validate_html(html, features)

    assert result.valid is True
    assert result.errors == []


def test_validate_html_rejects_missing_required_tags_and_duplicate_ids() -> None:
    features = FeatureModel()
    html = """<!DOCTYPE html><html><body><main id="same"></main><section id="same"></section></body></html>"""

    result = validate_html(html, features)

    assert result.valid is False
    assert "missing required tag: <head>" in result.errors
    assert "duplicate id: same" in result.errors


def test_validate_html_ignores_feature_content_requirements() -> None:
    features = FeatureModel()
    features.requirements.must_preserve = ["Required text"]
    html = "<!DOCTYPE html><html><head></head><body><main>Other text</main></body></html>"

    result = validate_html(html, features)

    assert result.valid is True
    assert result.errors == []


def test_validate_html_ignores_feature_links_and_media() -> None:
    features = FeatureModel(
        content={"links": [{"text": "Home", "href": "/home"}]},
        media={"images": [{"src": "/image.png", "alt": "Image"}]},
    )
    html = "<!DOCTYPE html><html><head></head><body><main></main></body></html>"

    result = validate_html(html, features)

    assert result.valid is True
    assert result.errors == []


def test_validate_html_passes_content_freedom_to_the_token_check() -> None:
    from wra.core.models import Heading

    features = FeatureModel(content={"headings": [Heading(level=1, text="효율성을 극대화해 드립니다")]})
    html = "<!DOCTYPE html><html><head><title>T</title></head><body><main><p>효율성을 극대화할 최적의 자재</p></main></body></html>"

    low = validate_html(html, features)
    madmax = validate_html(html, features, content_freedom="madmax")

    assert any("corrupted source token" in warning for warning in low.warnings)
    assert not any("corrupted source token" in warning for warning in madmax.warnings)
