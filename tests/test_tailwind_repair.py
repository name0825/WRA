from wra.generator.tailwind_repair import _CONFIG_GUARD, _remove_apply_token, repair_tailwind_v3_remnants

_V4_HEAD = '<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>'


def test_leaves_html_unchanged_without_the_v4_browser_build() -> None:
    html = f'<html><head><script src="https://cdn.tailwindcss.com"></script></head><body></body></html>'

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert repaired == html
    assert applied == []


def test_removes_the_v3_cdn_script_including_a_query_string() -> None:
    html = f'<html><head>{_V4_HEAD}<script src="https://cdn.tailwindcss.com?plugins=forms"></script></head><body></body></html>'

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert "cdn.tailwindcss.com" not in repaired
    assert "<script" in repaired
    assert len(applied) == 1
    assert "cdn.tailwindcss.com" in applied[0]


def test_guards_an_inline_tailwind_config_assignment() -> None:
    html = f'<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{}} }};</script></head><body></body></html>'

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert "window.tailwind = window.tailwind || {};" in repaired
    assert "tailwind.config = { theme: {} };" in repaired
    assert len(applied) == 1


def test_guard_insertion_is_idempotent() -> None:
    html = f'<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{}} }};</script></head><body></body></html>'

    once, _ = repair_tailwind_v3_remnants(html)
    twice, applied_again = repair_tailwind_v3_remnants(once)

    assert twice == once
    assert applied_again == []


def test_leaves_a_text_tailwindcss_script_untouched() -> None:
    html = f'<html><head>{_V4_HEAD}<script type="text/tailwindcss">tailwind.config = {{ theme: {{}} }};</script></head><body></body></html>'

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert repaired == html
    assert applied == []


def test_unclosed_script_tag_does_not_swallow_the_next_script() -> None:
    html = f'<html><head>{_V4_HEAD}<script src="https://cdn.tailwindcss.com"/><style>p{{}}</style><script>init();</script></head><body></body></html>'

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert repaired == html
    assert applied == []


def test_translates_nested_colors_and_default_into_a_theme_block() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ "
        "text: { main: '#111' }, primary: { DEFAULT: '#a', dark: '#b' } "
        "} } } };</script></head><body></body></html>"
    )

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert '<style type="text/tailwindcss">' in repaired
    assert "--color-text-main: #111;" in repaired
    assert "--color-primary: #a;" in repaired
    assert "--color-primary-dark: #b;" in repaired
    assert repaired.index('<style type="text/tailwindcss">') < repaired.index(_V4_HEAD)
    assert any("@theme" in message for message in applied)


def test_translates_font_family_array_joining_names_with_commas() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ fontFamily: {{ "
        "sans: ['Inter', 'Verdana', 'sans-serif'] } } } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert "--font-sans: Inter, Verdana, sans-serif;" in repaired


def test_translates_font_size_tuple_using_only_the_first_element() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ fontSize: {{ "
        "h1: ['2.5rem', { lineHeight: '1.2' }] } } } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert "--text-h1: 2.5rem;" in repaired
    assert "lineHeight" not in repaired.split('<style type="text/tailwindcss">')[1].split("</style>")[0]


def test_skips_a_key_with_an_unparseable_function_value() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ "
        "colors: { primary: '#5b6ee1' }, "
        "typography: (theme) => ({ DEFAULT: { css: { color: theme('colors.text') } } }) "
        "} } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert "--color-primary: #5b6ee1;" in repaired
    assert "typography" not in repaired.split('<style type="text/tailwindcss">')[1].split("</style>")[0]


def test_no_v4_build_leaves_tailwind_config_untouched() -> None:
    html = '<html><head><script>tailwind.config = { theme: { extend: { colors: { primary: "#5b6ee1" } } } };</script></head><body></body></html>'

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert repaired == html
    assert applied == []


def test_existing_theme_block_is_left_untouched() -> None:
    html = (
        f'<html><head>{_V4_HEAD}<style type="text/tailwindcss">@theme {{ --color-primary: #000; }}</style>'
        f"<script>{_CONFIG_GUARD}tailwind.config = {{ theme: {{ extend: {{ colors: {{ primary: '#5b6ee1' }} }} }} }};</script></head><body></body></html>"
    )

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert repaired == html
    assert applied == []


def test_theme_translation_is_idempotent() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ "
        "primary: '#5b6ee1' } } } };</script></head><body></body></html>"
    )

    once, _ = repair_tailwind_v3_remnants(html)
    twice, applied_again = repair_tailwind_v3_remnants(once)

    assert twice == once
    assert applied_again == []


def test_translates_fractional_spacing_key_with_escaped_dot() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ spacing: {{ "
        "'1.5': '0.375rem' } } } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert r"--spacing-1\.5: 0.375rem;" in repaired


def test_translates_slash_key_with_escaped_slash() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ spacing: {{ "
        "'1/2': '50%' } } } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert r"--spacing-1\/2: 50%;" in repaired


def test_skips_a_hostile_key_that_would_break_out_of_the_style_tag() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ spacing: {{ "
        "'x}</style><script>evil</script><style>y': '1rem', "
        "sm: '0.5rem' } } } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert "--spacing-sm: 0.5rem;" in repaired
    assert "</style><script>evil</script>" not in repaired.split('<style type="text/tailwindcss">')[1].split("</style>")[0]


def test_skips_a_key_containing_a_space() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>tailwind.config = {{ theme: {{ extend: {{ spacing: {{ "
        "'has space': '1rem', sm: '0.5rem' } } } };</script></head><body></body></html>"
    )

    repaired, _ = repair_tailwind_v3_remnants(html)

    assert "--spacing-sm: 0.5rem;" in repaired
    assert "has space" not in repaired.split('<style type="text/tailwindcss">')[1].split("</style>")[0]


def test_remove_apply_token_renames_a_known_v3_utility() -> None:
    html = '<style type="text/tailwindcss">.x { @apply break-words overflow-wrap-anywhere; }</style>'

    repaired = _remove_apply_token(html, "overflow-wrap-anywhere")

    assert "@apply break-words wrap-anywhere;" in repaired
    assert "overflow-wrap-anywhere" not in repaired


def test_remove_apply_token_deletes_an_unknown_utility() -> None:
    html = '<style type="text/tailwindcss">.tag { @apply bg-[#c3bbdd] bg-opacity-20 text-gray-800; }</style>'

    repaired = _remove_apply_token(html, "bg-opacity-20")

    assert "@apply bg-[#c3bbdd] text-gray-800;" in repaired


def test_remove_apply_token_drops_the_line_when_apply_ends_empty() -> None:
    html = '<style type="text/tailwindcss">\n.x {\n    @apply bg-opacity-20;\n}\n</style>'

    repaired = _remove_apply_token(html, "bg-opacity-20")

    assert "@apply" not in repaired
    assert repaired == '<style type="text/tailwindcss">\n.x {\n}\n</style>'


def test_remove_apply_token_ignores_matches_outside_apply() -> None:
    html = '<style type="text/tailwindcss">/* bg-opacity-20 mentioned in a comment */ .x { @apply text-sm; }</style>'

    repaired = _remove_apply_token(html, "bg-opacity-20")

    assert repaired == html


def test_remove_apply_token_ignores_matches_outside_tailwindcss_style() -> None:
    html = '<style>.x { @apply bg-opacity-20; }</style>'

    repaired = _remove_apply_token(html, "bg-opacity-20")

    assert repaired == html


def test_malformed_config_is_left_untouched_without_raising() -> None:
    html = (
        f"<html><head>{_V4_HEAD}<script>{_CONFIG_GUARD}tailwind.config = {{ theme: {{ extend: {{ "
        "colors: { primary #5b6ee1 } } } };</script></head><body></body></html>"
    )

    repaired, applied = repair_tailwind_v3_remnants(html)

    assert repaired == html
    assert applied == []
