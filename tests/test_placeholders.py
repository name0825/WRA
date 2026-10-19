import pytest

from wra.core.models import Content, FeatureModel, Media, SourceAssets
from wra.utils.placeholders import PlaceholderMap, protect_features, protect_text, restore


def test_protect_features_replaces_email_phone_and_url() -> None:
    features = FeatureModel(content=Content(paragraphs=["Contact us at sylee@im@example.com or +1 (555) 123-4567.", "Visit https://example.com/contact for more."]))

    protected, mapping = protect_features(features)

    text = " ".join(protected.content.paragraphs)
    assert "sylee@im@example.com" not in text
    assert "+1 (555) 123-4567" not in text
    assert "https://example.com/contact" not in text
    assert mapping

    restored, unresolved = restore(text, mapping)
    assert unresolved == 0
    assert "sylee@im@example.com" in restored
    assert "+1 (555) 123-4567" in restored
    assert "https://example.com/contact" in restored


def test_protect_features_leaves_prose_and_bare_numbers_untouched() -> None:
    features = FeatureModel(content=Content(paragraphs=["Serving over 80 cities since 2019.", "Prices start at 100."]))

    protected, mapping = protect_features(features)

    assert protected.content.paragraphs == features.content.paragraphs
    assert not mapping


@pytest.mark.parametrize(
    "text",
    [
        "2024",
        "Founded in 1999-2004.",
        "$1,234,567.89",
        "Save 50-100 dollars today.",
        "ISO date: 2024-01-01 10:30:00",
        "order id 123-456-7890-12",
        "price range 1000 - 2000 - 3000 won",
        "Year: 1998",
        "Range: 2020-2024",
        "Date: 2024-01-01",
        "Amount: 1,200,000",
    ],
)
def test_phone_regex_rejects_years_prices_and_ranges(text: str) -> None:
    features = FeatureModel(content=Content(paragraphs=[text]))

    protected, mapping = protect_features(features)

    assert protected.content.paragraphs == [text]
    assert not mapping


@pytest.mark.parametrize(
    "text",
    [
        "Call +82 10-1234-5678 now.",
        "Call +1 (555) 123-4567 now.",
        "Call 010-1234-5678 now.",
        "Call 02-123-4567 now.",
        "Call (02) 1234 5678 now.",
        "Call 555-123-4567 now.",
        "tel:+82 10-1234-5678",
        "tel:010-1234-5678",
    ],
)
def test_phone_regex_accepts_valid_phone_numbers(text: str) -> None:
    features = FeatureModel(content=Content(paragraphs=[text]))

    protected, mapping = protect_features(features)

    assert protected.content.paragraphs != [text]
    assert mapping


def test_restore_handles_markup_split_token() -> None:
    mapping = PlaceholderMap(values={"WRAREF2": "+1 (555) 123-4567"})

    restored, unresolved = restore('href="WRA</em>REF2"', mapping)

    assert restored == 'href="+1 (555) 123-4567"'
    assert unresolved == 0


@pytest.mark.parametrize(
    "token",
    ["WRA_REF2", "wra ref 2", "WRA<b></b>REF<b></b>2", "WRA-REF-2", "WRA.REF.2"],
)
def test_restore_handles_mangled_token_variants(token: str) -> None:
    mapping = PlaceholderMap(values={"WRAREF2": "+1 (555) 123-4567"})

    restored, unresolved = restore(token, mapping)

    assert restored == "+1 (555) 123-4567"
    assert unresolved == 0


def test_restore_strips_unknown_mangled_token() -> None:
    mapping = PlaceholderMap(values={"WRAREF2": "+1 (555) 123-4567"})

    restored, unresolved = restore("WRA</em>REF99", mapping)

    assert restored == ""
    assert unresolved == 1


def test_restore_does_not_match_unrelated_prose_between_wra_and_ref() -> None:
    mapping = PlaceholderMap(values={"WRAREF2": "+1 (555) 123-4567"})
    text = "The WRA team will REFer you to support."

    restored, unresolved = restore(text, mapping)

    assert restored == text
    assert unresolved == 0


def test_restore_recovers_a_mangled_token_letters() -> None:
    mapping = PlaceholderMap(values={"WRAREF9": "wjyoon@example.com"})

    restored, unresolved = restore("Contact WRATR9 for details.", mapping)

    assert restored == "Contact wjyoon@example.com for details."
    assert unresolved == 0


def test_restore_recovers_a_mangled_token_with_separators() -> None:
    mapping = PlaceholderMap(values={"WRAREF9": "wjyoon@example.com"})

    restored, unresolved = restore("Contact WRA_TR 9 for details.", mapping)

    assert restored == "Contact wjyoon@example.com for details."
    assert unresolved == 0


def test_restore_leaves_an_unmapped_mangled_token_untouched() -> None:
    mapping = PlaceholderMap(values={"WRAREF9": "wjyoon@example.com"})

    restored, unresolved = restore("See WRAXY99 for details.", mapping)

    assert restored == "See WRAXY99 for details."
    assert unresolved == 0


def test_restore_exact_ref_token_behavior_is_unchanged() -> None:
    mapping = PlaceholderMap(values={"WRAREF3": "a@example.com"})

    restored, unresolved = restore("Contact WRAREF3 now.", mapping)

    assert restored == "Contact a@example.com now."
    assert unresolved == 0


def test_restore_does_not_touch_a_real_word_with_no_boundary() -> None:
    mapping = PlaceholderMap(values={"WRAREF1": "a@example.com"})

    restored, unresolved = restore("UNWRAP1 stays as-is.", mapping)

    assert restored == "UNWRAP1 stays as-is."
    assert unresolved == 0


def test_restore_does_not_touch_wrap1_without_a_mapping() -> None:
    mapping = PlaceholderMap(values={})

    restored, unresolved = restore("WRAP1 stays as-is.", mapping)

    assert restored == "WRAP1 stays as-is."
    assert unresolved == 0


@pytest.mark.parametrize(
    ("token", "wra_number"),
    [("See WRAXYZ9 for details.", "9"), ("See WRAABC123 for details.", "123")],
)
def test_restore_leaves_a_letters_without_ref_untouched_even_with_a_matching_mapping(token: str, wra_number: str) -> None:
    """Letters that share none of R/E/F are too common in ordinary text to treat as
    damage, even when the mapping happens to have the matching number.
    """
    mapping = PlaceholderMap(values={f"WRAREF{wra_number}": "a@example.com"})

    restored, unresolved = restore(token, mapping)

    assert restored == token
    assert unresolved == 0


def test_restore_does_not_touch_wrap1_even_with_a_matching_mapping() -> None:
    """A single mangled letter that is not R/E/F is too common in ordinary words
    (e.g. "WRAP1") to treat as damage, even if the mapping happens to have the number.
    """
    mapping = PlaceholderMap(values={"WRAREF1": "a@example.com"})

    restored, unresolved = restore("WRAP1 stays as-is.", mapping)

    assert restored == "WRAP1 stays as-is."
    assert unresolved == 0


def test_repeated_literal_maps_to_same_token() -> None:
    features = FeatureModel(content=Content(paragraphs=["Email a@example.com now.", "Also email a@example.com later."]))

    protected, mapping = protect_features(features)

    first, second = protected.content.paragraphs
    first_token = first.split()[1]
    second_token = second.split()[2]
    assert first_token == second_token
    assert len(mapping.values) == 1


def test_protect_features_excludes_source_assets_and_media() -> None:
    features = FeatureModel(
        source_assets=SourceAssets(assets=[]),
        media=Media(urls=["https://cdn.example.com/asset@example.com.png"]),
        content=Content(paragraphs=["Reach us at contact@example.com."]),
    )

    protected, mapping = protect_features(features)

    assert protected.media.urls == features.media.urls
    assert "WRAREF" not in protected.media.urls[0]
    assert "contact@example.com" not in protected.content.paragraphs[0]


def test_restore_strips_unknown_token_and_counts_it() -> None:
    mapping = PlaceholderMap(values={"WRAREF1": "a@example.com"})

    restored, unresolved = restore("Contact WRAREF1 or WRAREF2.", mapping)

    assert restored == "Contact a@example.com or ."
    assert unresolved == 1


def test_restore_is_case_insensitive() -> None:
    mapping = PlaceholderMap(values={"WRAREF1": "a@example.com"})

    restored, unresolved = restore("Contact wraref1.", mapping)

    assert restored == "Contact a@example.com."
    assert unresolved == 0


def test_anchor_and_relative_paths_are_never_protected() -> None:
    features = FeatureModel(content=Content(links=[{"text": "Home", "href": "#top"}, {"text": "About", "href": "/about"}]))

    protected, mapping = protect_features(features)

    assert protected.content.links[0].href == "#top"
    assert protected.content.links[1].href == "/about"
    assert not mapping


def test_protect_text_applies_existing_mapping() -> None:
    features = FeatureModel(content=Content(paragraphs=["Contact a@example.com."]))
    _, mapping = protect_features(features)

    protected_html = protect_text("<p>Email: a@example.com</p>", mapping)

    assert "a@example.com" not in protected_html
    assert list(mapping.values)[0] in protected_html
