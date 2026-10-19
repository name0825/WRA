from wra.validator.syntax import find_css_truncation, find_invalid_href_schemes, find_malformed_css_values, find_undefined_custom_properties


def test_reports_a_css_block_with_an_unclosed_rule() -> None:
    html = "<style>h1 { color: red; } h2 { font-size: clamp(1.8rem, 5vw, 2.2</style>"

    findings = find_css_truncation(html)

    assert len(findings) == 1
    assert "unbalanced" in findings[0]


def test_accepts_a_css_block_with_balanced_braces() -> None:
    html = "<style>h1 { color: red; } h2 { font-size: 1rem; }</style>"

    assert find_css_truncation(html) == []


def test_accepts_a_page_with_no_css_at_all() -> None:
    assert find_css_truncation("<html><body>hello</body></html>") == []


def test_reports_a_non_hex_custom_property_value() -> None:
    html = "<style>:root { --accent-color: #4a4a4mal; }</style>"

    findings = find_malformed_css_values(html)

    assert len(findings) == 1
    assert "#4a4a4mal" in findings[0]


def test_reports_a_hyphen_in_place_of_a_decimal_point() -> None:
    html = "<style>p { font-size: 0-875rem; }</style>"

    findings = find_malformed_css_values(html)

    assert len(findings) == 1
    assert "0-875rem" in findings[0]


def test_accepts_a_valid_hex_colour() -> None:
    html = "<style>:root { --accent-color: #4a4a4a; }</style>"

    assert find_malformed_css_values(html) == []


def test_accepts_a_valid_decimal_value() -> None:
    html = "<style>p { font-size: 0.875rem; }</style>"

    assert find_malformed_css_values(html) == []


def test_ignores_a_url_fragment_reference() -> None:
    html = "<style>.icon { fill: url(#gradient1); }</style>"

    assert find_malformed_css_values(html) == []


def test_ignores_calc_subtraction() -> None:
    html = "<style>.box { width: calc(100% - 20px); }</style>"

    assert find_malformed_css_values(html) == []


def test_checks_inline_style_attributes_too() -> None:
    html = '<div style="color: #zzzzzz;"></div>'

    findings = find_malformed_css_values(html)

    assert len(findings) == 1


def test_ignores_a_hash_inside_a_string_literal() -> None:
    html = '<style>.x { content: "#new"; }</style>'

    assert find_malformed_css_values(html) == []


def test_ignores_a_hash_inside_a_single_char_string_literal() -> None:
    html = "<style>.x { content: \"#1\"; }</style>"

    assert find_malformed_css_values(html) == []


def test_ignores_a_bad_hex_inside_a_comment() -> None:
    html = "<style>/* color: #wrong */ .x { color: #fff; }</style>"

    assert find_malformed_css_values(html) == []


def test_reports_an_unknown_href_scheme() -> None:
    html = '<a href="html:hsong@example.com">Email</a>'

    findings = find_invalid_href_schemes(html)

    assert len(findings) == 1
    assert "html:" in findings[0]


def test_accepts_http_and_https() -> None:
    html = '<a href="http://example.com">A</a><a href="https://example.com">B</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_mailto() -> None:
    html = '<a href="mailto:someone@example.com">Email</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_tel() -> None:
    html = '<a href="tel:+821012345678">Call</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_a_fragment() -> None:
    html = '<a href="#section-id">Jump</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_a_relative_path() -> None:
    html = '<a href="assets/img.png">Image</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_sms_geo_and_fax() -> None:
    html = '<a href="sms:+821012345678">S</a><a href="geo:37.5,127.0">G</a><a href="fax:+82321234567">F</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_a_protocol_relative_url() -> None:
    html = '<a href="//example.com/x">Link</a>'

    assert find_invalid_href_schemes(html) == []


def test_accepts_a_bare_query_href() -> None:
    html = '<a href="?query=1">Link</a>'

    assert find_invalid_href_schemes(html) == []


def test_flags_javascript_scheme_as_intentional() -> None:
    html = '<a href="javascript:void(0)">Link</a>'

    findings = find_invalid_href_schemes(html)

    assert len(findings) == 1
    assert "on purpose" in findings[0]


def test_reports_an_undeclared_custom_property_reference() -> None:
    html = "<style>:root { --border: 1px; } .x { color: var(---border); }</style>"

    findings = find_undefined_custom_properties(html)

    assert len(findings) == 1
    assert "---border" in findings[0]


def test_accepts_a_declared_custom_property_reference() -> None:
    html = "<style>:root { --accent: #4a4a4a; } .x { color: var(--accent); }</style>"

    assert find_undefined_custom_properties(html) == []


def test_accepts_an_undeclared_reference_with_a_fallback() -> None:
    html = "<style>.x { color: var(--missing, #000); }</style>"

    assert find_undefined_custom_properties(html) == []


def test_accepts_a_property_declared_via_at_property() -> None:
    html = "<style>@property --accent { syntax: '<color>'; inherits: false; initial-value: #000; } .x { color: var(--accent); }</style>"

    assert find_undefined_custom_properties(html) == []


def test_accepts_a_property_declared_via_script() -> None:
    html = "<style>.x { color: var(--accent); }</style><script>document.documentElement.style.setProperty('--accent', '#000');</script>"

    assert find_undefined_custom_properties(html) == []


def test_skips_the_check_when_a_non_font_stylesheet_is_linked() -> None:
    html = '<link rel="stylesheet" href="https://cdn.example.com/framework.css"><style>.x { color: var(--missing); }</style>'

    assert find_undefined_custom_properties(html) == []


def test_still_checks_when_only_a_google_fonts_stylesheet_is_linked() -> None:
    html = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans"><style>.x { color: var(--missing); }</style>'

    findings = find_undefined_custom_properties(html)

    assert len(findings) == 1
    assert "--missing" in findings[0]
