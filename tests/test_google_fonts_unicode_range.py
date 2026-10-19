import time
from unittest.mock import patch

from wra.browser.evaluator import (
    _iter_font_face_urls,
    _localize_google_fonts,
    _parse_unicode_range,
    _unicode_range_matches,
    _visible_text_codepoints,
)


def test_parses_a_simple_range() -> None:
    assert _parse_unicode_range("U+AC00-D7A3") == [(0xAC00, 0xD7A3)]


def test_parses_a_wildcard_range() -> None:
    assert _parse_unicode_range("U+4??") == [(0x400, 0x4FF)]


def test_parses_a_single_codepoint_and_multi_token_list() -> None:
    assert _parse_unicode_range("U+0-FF, U+131, U+152-153") == [
        (0x0, 0xFF),
        (0x131, 0x131),
        (0x152, 0x153),
    ]


def test_visible_text_extracts_body_text_and_labelled_attributes() -> None:
    html = (
        '<html><head><style>body{content:"가"}</style>'
        '<script>var x="나";</script></head>'
        '<body><h1>가나</h1>'
        '<img src="x.png" alt="다"><input placeholder="라">'
        '</body></html>'
    )
    codepoints = _visible_text_codepoints(html)
    assert ord("가") in codepoints  # from body text
    assert ord("나") in codepoints  # from body text
    assert ord("다") in codepoints  # from alt=
    assert ord("라") in codepoints  # from placeholder=
    # CSS/JS-only text must not leak in as "visible" text.
    # (both chars above already appear in the body, so use a CSS/JS-only one)
    css_only_html = '<style>body{content:"ㅎ"}</style><body>hi</body>'
    assert ord("ㅎ") not in _visible_text_codepoints(css_only_html)


def test_unicode_range_matches_only_when_a_page_codepoint_is_in_range() -> None:
    assert _unicode_range_matches("U+AC00-D7A3", [ord("가")]) is True
    assert _unicode_range_matches("U+AC00-D7A3", [ord("A")]) is False
    assert _unicode_range_matches("U+AC00-D7A3", []) is False


def test_visible_text_codepoints_handles_pathological_input_quickly() -> None:
    start = time.perf_counter()
    _visible_text_codepoints("<" * 2_000_000)
    _visible_text_codepoints("<script>" * 200_000)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0


def test_unclosed_style_repeated_stays_linear() -> None:
    start = time.perf_counter()
    _visible_text_codepoints("<style></style>" * 200_000)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0


def test_unclosed_script_repeated_stays_linear() -> None:
    start = time.perf_counter()
    _visible_text_codepoints("<script></script>" * 200_000)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0


def test_multibyte_lowercasing_does_not_shift_script_boundaries() -> None:
    html = "İ" * 10 + "<script>SECRETXYZ</script>visible"
    codepoints = _visible_text_codepoints(html)

    for ch in "SECRETXYZ":
        assert ord(ch) not in codepoints
    for ch in "visible":
        assert ord(ch) in codepoints


def test_large_input_matches_quickly() -> None:
    # 20k distinct code points, sorted once (as the caller now does per-page),
    # then checked against 500 unicode-range slices x 100 tokens each: the
    # scale that reached ~10**9 linear-scan comparisons before the fix.
    sorted_points = sorted(set(range(0x4E00, 0x4E00 + 20000)))
    range_text = ",".join(f"U+{i:X}-{i:X}" for i in range(0x0, 0x0 + 100))

    start = time.perf_counter()
    for _ in range(500):
        assert _unicode_range_matches(range_text, sorted_points) is False
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0


def _font_face(url: str, unicode_range: str | None) -> str:
    range_decl = f"unicode-range:{unicode_range};" if unicode_range else ""
    return f"@font-face{{font-family:'Noto Sans KR';{range_decl}src:url({url}) format('woff2');}}"


def test_only_intersecting_slices_are_fetched() -> None:
    matching_url = "https://fonts.gstatic.com/s/notosanskr/v39/match.woff2"
    non_matching_url = "https://fonts.gstatic.com/s/notosanskr/v39/nomatch.woff2"
    css_text = _font_face(matching_url, "U+AC00-D7A3") + _font_face(non_matching_url, "U+0-FF")

    sorted_page_points = [ord("가")]  # a Korean syllable, in the matching range only
    urls = list(_iter_font_face_urls(css_text, sorted_page_points))

    assert urls == [matching_url]


def test_a_block_without_unicode_range_is_always_fetched() -> None:
    url = "https://fonts.gstatic.com/s/roboto/v30/plain.woff2"
    css_text = _font_face(url, None)

    urls = list(_iter_font_face_urls(css_text, [ord("z")]))

    assert urls == [url]


def test_localize_skips_visible_text_scan_when_no_font_link_present(tmp_path) -> None:
    html = "<html><body>no google fonts here</body></html>"

    with patch("wra.browser.evaluator._visible_text_codepoints") as mock_scan:
        result = _localize_google_fonts(html, tmp_path, tmp_path)

    mock_scan.assert_not_called()
    assert result == html


def test_non_utf8_css_falls_back_to_leaving_url_untouched(tmp_path) -> None:
    css_url = "https://fonts.googleapis.com/css2?family=Noto+Sans+KR"
    html = f'<link href="{css_url}">'
    bad_css = tmp_path / "bad.css"
    bad_css.write_bytes(b"\xff\xfe\x00\x01")

    with patch("wra.browser.evaluator.fetch_cached", return_value=bad_css):
        result = _localize_google_fonts(html, tmp_path, tmp_path)

    assert result == html
