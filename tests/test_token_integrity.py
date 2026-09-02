from wra.core.models import Button, Content, FeatureModel, Heading
from wra.validator.tokens import find_corrupted_tokens


def make_features() -> FeatureModel:
    return FeatureModel(
        content=Content(
            headings=[
                Heading(level=1, text="FASTENER · BEARING · CUTTING · MRO"),
                Heading(level=2, text="취급품목 안내"),
                Heading(level=3, text="2021.11 제3회 캠프"),
                Heading(level=4, text="2022.11 제4회 캠프"),
            ],
            buttons=[Button(text="480-88-03967", href="#biz")],
        )
    )


def test_reports_a_corrupted_uppercase_token() -> None:
    findings = find_corrupted_tokens("<p>FASTER · BEARING · CUTTING · MRO</p>", make_features())

    assert any("FASTENER" in finding and "FASTER" in finding for finding in findings)


def test_reports_a_corrupted_cjk_token() -> None:
    findings = find_corrupted_tokens("<p>취급목목 안내</p>", make_features())

    assert any("취급품목" in finding and "취급목목" in finding for finding in findings)


def test_reports_a_corrupted_identifier() -> None:
    findings = find_corrupted_tokens("<p>480-88-03968</p>", make_features())

    assert any("480-88-03967" in finding for finding in findings)


def test_accepts_a_dropped_token() -> None:
    assert find_corrupted_tokens("<p>BEARING · CUTTING</p>", make_features()) == []


def test_accepts_reworded_prose() -> None:
    assert find_corrupted_tokens("<p>Precision components, sourced and quoted fast.</p>", make_features()) == []


def test_does_not_confuse_two_distinct_source_tokens() -> None:
    html = "<p>2021.11 제3회 캠프</p><p>2022.11 제4회 캠프</p>"

    assert find_corrupted_tokens(html, make_features()) == []


def test_ignores_script_and_style_content() -> None:
    assert find_corrupted_tokens("<script>var x = 'FASTER';</script>", make_features()) == []


def test_ignores_typographic_hyphen_variants() -> None:
    features = FeatureModel(content=Content(headings=[Heading(level=2, text="TA‑01 / TA‑02")]))

    assert find_corrupted_tokens("<p>TA-01 / TA-02</p>", features) == []
