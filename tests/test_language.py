from wra.validator.language import find_language_drift, infer_language

KO = "안녕하세요 저는 백엔드 개발자를 목표로 공부하고 있습니다. " * 12
EN = "Hello I am a backend developer who builds and runs my own servers. " * 12


def test_infers_korean_and_english() -> None:
    assert infer_language(KO) == "ko"
    assert infer_language(EN) == "en"


def test_mixed_or_empty_text_has_no_language() -> None:
    assert infer_language("") is None
    assert infer_language("12345 !!!") is None
    assert infer_language("가나다라마 abcdefghij klmnopqrst") is None


def test_a_latin_page_for_a_korean_brief_is_drift() -> None:
    assert find_language_drift(f"<p>{EN}</p>", "ko") == ["output language drifted: expected ko, page is mostly Latin"]


def test_a_korean_page_for_a_korean_brief_is_fine() -> None:
    assert find_language_drift(f"<p>{KO}</p>", "ko") == []


def test_a_mostly_korean_page_for_an_english_brief_is_drift() -> None:
    assert find_language_drift(f"<p>{KO}</p>", "en") == ["output language drifted: expected en, page is mostly Hangul"]


def test_unknown_language_or_short_text_is_skipped() -> None:
    assert find_language_drift(f"<p>{EN}</p>", None) == []
    assert find_language_drift("<p>Hello</p>", "ko") == []


def test_code_urls_and_scripts_are_ignored() -> None:
    html = f"<p>{KO}</p><pre>{EN * 5}</pre><script>{EN * 5}</script><p>https://example.com/{'a' * 400}</p>"

    assert find_language_drift(html, "ko") == []
