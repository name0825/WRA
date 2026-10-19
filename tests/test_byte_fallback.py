from wra.llm.byte_fallback import strip_byte_fallback_markers


def test_no_marker_returns_original_unchanged() -> None:
    text = "hello world"
    result, count = strip_byte_fallback_markers(text)
    assert result is text
    assert count == 0


def test_single_marker_dropped() -> None:
    result, count = strip_byte_fallback_markers("HRI, 202<0xC2>7")
    assert result == "HRI, 2027"
    assert count == 1


def test_run_decodes_to_utf8_character() -> None:
    result, count = strip_byte_fallback_markers("<0xC3><0xAD>")
    assert result == "í"
    assert count == 2


def test_run_decodes_nbsp_to_plain_space() -> None:
    result, count = strip_byte_fallback_markers("a<0xC2><0xA0>b")
    assert result == "a b"
    assert count == 2


def test_run_decodes_format_character_and_removes_it() -> None:
    result, count = strip_byte_fallback_markers("a<0xE2><0x81><0xAA>b")
    assert result == "ab"
    assert count == 3


def test_marker_lookalikes_are_left_alone() -> None:
    for text in ("<0xZZ>", "< 0xC2 >", "0xC2", "<0xC2", "0xC2>", "<0xC>", "<0xC22>"):
        result, count = strip_byte_fallback_markers(text)
        assert result is text
        assert count == 0
