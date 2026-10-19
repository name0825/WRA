import pytest

from wra.browser.models import BrowserEvaluationResult,BrowserViewportResult, EdgeHuggingTextElement, HeaderLogoElement, HeaderOverlapPair, OccludedTextElement, OverflowElement, VerticalGapBand, WrappedTextElement
from wra.core.models import FeatureModel, Image, Media
from wra.validator.visual import EDGE_HUGGING_TEXT_PREFIX, HEADER_OVERLAP_PREFIX, IMAGES_DROPPED_PREFIX, IMAGES_MISSING_PREFIX, OCCLUDED_TEXT_PREFIX, OVERSIZED_HEADER_LOGO_PREFIX, RENDER_COLLAPSE_PREFIX, VERTICAL_GAP_PREFIX, WIDE_TEXT_WIDTH_PREFIX, find_broken_image_markup, find_edge_hugging_text_defects, find_exposed_markup, find_header_overlap_defects, find_missing_content_images, find_occluded_text_defects, find_oversized_header_logo_defects, find_overflow_defects, find_render_collapse_defects, find_vertical_gap_defects, find_visual_defects, find_wide_text_width_defects, _is_unstyled


def test_truncated_css_is_reported_as_a_render_collapse() -> None:
    html = "<style>h2 { font-size: clamp(1.8rem, 5vw, 2.2</style>"

    findings = find_render_collapse_defects(html, None)

    assert any(finding.startswith(RENDER_COLLAPSE_PREFIX) for finding in findings)


def test_a_narrow_first_screen_is_reported_as_a_render_collapse() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, viewport_width=1440, first_screen_max_right=300)],
    )

    findings = find_render_collapse_defects("<style>body{color:red}</style>", browser_result)

    assert any(finding.startswith(RENDER_COLLAPSE_PREFIX) for finding in findings)


def test_a_full_width_first_screen_is_not_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, viewport_width=1440, first_screen_max_right=1392)],
    )

    assert find_render_collapse_defects("<style>body{color:red}</style>", browser_result) == []


def test_char_level_text_wrapping_is_reported_as_a_render_collapse() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844, viewport_width=390,
            wrapped_text_elements=[WrappedTextElement(selector="nav a", text="Services", line_count=8, char_count=8)],
        )],
    )

    findings = find_render_collapse_defects("<style>body{color:red}</style>", browser_result)

    assert any(finding.startswith(RENDER_COLLAPSE_PREFIX) for finding in findings)


def test_a_stacked_element_is_reported_as_a_render_collapse() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844, viewport_width=390,
            wrapped_text_elements=[WrappedTextElement(selector="nav a", text="이야기", line_count=3, char_count=3)],
        )],
    )

    findings = find_render_collapse_defects("<style>body{color:red}</style>", browser_result)

    assert any(finding.startswith(RENDER_COLLAPSE_PREFIX) for finding in findings)


def test_a_single_squeezed_element_is_not_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844, viewport_width=390,
            wrapped_text_elements=[WrappedTextElement(selector="nav a", text="Consultations", line_count=4, char_count=14)],
        )],
    )

    findings = find_render_collapse_defects("<style>body{color:red}</style>", browser_result)

    assert not any(finding.startswith(RENDER_COLLAPSE_PREFIX) for finding in findings)


def test_two_squeezed_elements_are_reported_as_a_render_collapse() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844, viewport_width=390,
            wrapped_text_elements=[
                WrappedTextElement(selector="nav a", text="Consultations", line_count=4, char_count=14),
                WrappedTextElement(selector="nav a", text="Portfolio", line_count=3, char_count=9),
            ],
        )],
    )

    findings = find_render_collapse_defects("<style>body{color:red}</style>", browser_result)

    assert any(finding.startswith(RENDER_COLLAPSE_PREFIX) for finding in findings)


def test_a_full_viewport_blank_band_is_reported_as_a_vertical_gap() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="desktop", width=1440, height=900,
            vertical_gap_bands=[VerticalGapBand(top=500, height=920)],
        )],
    )

    findings = find_vertical_gap_defects(browser_result)

    assert any(finding.startswith(VERTICAL_GAP_PREFIX) for finding in findings)


def test_a_sub_viewport_blank_band_is_not_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="desktop", width=1440, height=900,
            vertical_gap_bands=[VerticalGapBand(top=500, height=300)],
        )],
    )

    assert find_vertical_gap_defects(browser_result) == []


def test_an_oversized_header_logo_is_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="desktop", width=1440, height=900,
            header_logo_elements=[HeaderLogoElement(selector="img.logo", width=800, height=420)],
        )],
    )

    findings = find_oversized_header_logo_defects(browser_result)

    assert any(finding.startswith(OVERSIZED_HEADER_LOGO_PREFIX) for finding in findings)


def test_no_header_logo_elements_means_no_finding() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900)],
    )

    assert find_oversized_header_logo_defects(browser_result) == []


def test_edge_hugging_text_is_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            edge_hugging_text_elements=[EdgeHuggingTextElement(selector="h3", section="section.features", left=0, right=80, text="Our Services")],
        )],
    )

    findings = find_edge_hugging_text_defects(browser_result)

    assert any(finding.startswith(EDGE_HUGGING_TEXT_PREFIX) for finding in findings)


def test_no_edge_hugging_text_elements_means_no_finding() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="mobile", width=390, height=844)],
    )

    assert find_edge_hugging_text_defects(browser_result) == []


def test_edge_hugging_text_is_not_reported_when_browser_result_is_none() -> None:
    assert find_edge_hugging_text_defects(None) == []


def test_edge_hugging_text_is_not_reported_when_browser_result_is_disabled() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=False,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            edge_hugging_text_elements=[EdgeHuggingTextElement(selector="h3", section="section.features", left=0, right=80, text="Our Services")],
        )],
    )

    assert find_edge_hugging_text_defects(browser_result) == []


def test_header_overlap_is_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            header_overlap_pairs=[HeaderOverlapPair(a="a.text-xl:Acme", b="a:Home", ratio=0.88)],
        )],
    )

    findings = find_header_overlap_defects(browser_result)

    assert any(finding.startswith(HEADER_OVERLAP_PREFIX) for finding in findings)


def test_no_header_overlap_pairs_means_no_finding() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="mobile", width=390, height=844)],
    )

    assert find_header_overlap_defects(browser_result) == []


def test_header_overlap_is_not_reported_when_browser_result_is_none() -> None:
    assert find_header_overlap_defects(None) == []


def test_header_overlap_is_not_reported_when_browser_result_is_disabled() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=False,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            header_overlap_pairs=[HeaderOverlapPair(a="a.text-xl:Acme", b="a:Home", ratio=0.88)],
        )],
    )

    assert find_header_overlap_defects(browser_result) == []


def test_occluded_text_is_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            occluded_text_elements=[OccludedTextElement(selector="h1", text="Welcome", occluder="div.container.nav-inner")],
        )],
    )

    findings = find_occluded_text_defects(browser_result)

    assert any(finding.startswith(OCCLUDED_TEXT_PREFIX) for finding in findings)


def test_no_occluded_text_elements_means_no_finding() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="mobile", width=390, height=844)],
    )

    assert find_occluded_text_defects(browser_result) == []


def test_occluded_text_is_not_reported_when_browser_result_is_none() -> None:
    assert find_occluded_text_defects(None) == []


def test_occluded_text_is_not_reported_when_browser_result_is_disabled() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=False,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            occluded_text_elements=[OccludedTextElement(selector="h1", text="Welcome", occluder="div.container.nav-inner")],
        )],
    )

    assert find_occluded_text_defects(browser_result) == []


def test_occluded_text_finding_is_wired_into_find_visual_defects() -> None:
    html = "<html><head><style>body { color: red; }</style></head><body></body></html>"
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(
            name="mobile", width=390, height=844,
            occluded_text_elements=[OccludedTextElement(selector="h1", text="Welcome", occluder="div.container.nav-inner")],
        )],
    )

    findings = find_visual_defects(html, _features([]), browser_result)

    assert any(finding.startswith(OCCLUDED_TEXT_PREFIX) for finding in findings)


def _features(srcs: list[str]) -> FeatureModel:
    return FeatureModel(media=Media(images=[Image(src=src) for src in srcs]))


_PHOTOS = ["assets/Logo.png", "assets/drill.png", "assets/endmill.png", "assets/holder.png"]
_STYLED = "<style>body { color: red; }</style>"


def test_all_provided_images_used_gives_no_missing_images_finding() -> None:
    html = f"<html><head>{_STYLED}</head><body>" + "".join(f'<img src="{src}">' for src in _PHOTOS) + "</body></html>"

    assert find_visual_defects(html, _features(_PHOTOS)) == []


def test_kept_logo_does_not_hide_dropped_content_images() -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="assets/Logo.png"><img src="assets/drill.png"></body></html>'

    findings = find_visual_defects(html, _features(_PHOTOS))

    assert findings == [f"{IMAGES_MISSING_PREFIX} 2 of 3 provided content image(s) are not used in the generated HTML: endmill.png, holder.png"]


def test_dropped_logo_alone_is_not_a_missing_content_image() -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="assets/drill.png"><img src="assets/endmill.png"><img src="assets/holder.png"></body></html>'

    assert find_visual_defects(html, _features(_PHOTOS)) == []


def test_similar_filename_does_not_count_as_the_image_being_used() -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="assets/11.jpg"><img src="assets/x1.jpg"></body></html>'

    findings = find_visual_defects(html, _features(["assets/1.jpg", "assets/11.jpg"]))

    assert findings == [f"{IMAGES_MISSING_PREFIX} 1 of 2 provided content image(s) are not used in the generated HTML: 1.jpg"]


def test_image_referenced_only_by_a_longer_name_counts_as_dropped() -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="assets/11.jpg"></body></html>'

    findings = find_visual_defects(html, _features(["assets/1.jpg"]))

    assert findings == [f"{IMAGES_DROPPED_PREFIX} none of the 1 source image(s) are referenced in the generated HTML"]


@pytest.mark.parametrize(
    ("src", "used"),
    [
        ("assets/hero%20image.png", "assets/hero image.png"),
        ("assets/%ED%9E%88%EC%96%B4%EB%A1%9C.png", "assets/히어로.png"),
        ("assets/hero image.png", "assets/hero%20image.png"),
        ("assets/hero%20image.png", "assets/hero%20image.png"),
    ],
)
def test_percent_encoding_does_not_hide_a_used_image(src: str, used: str) -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="{used}"></body></html>'

    assert find_missing_content_images(html, _features([src])) == []
    assert find_visual_defects(html, _features([src])) == []


def test_percent_decoding_still_reports_a_genuinely_missing_image() -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="assets/other image.png"></body></html>'

    assert find_missing_content_images(html, _features(["assets/hero%20image.png"]))


def test_logo_filter_matches_a_word_token_not_a_substring() -> None:
    html = f'<html><head>{_STYLED}</head><body></body></html>'
    srcs = ["a/catalogo.png", "a/site-logo.png", "a/logo_white.png", "a/Logo2.png"]

    findings = find_missing_content_images(html, _features(srcs))

    assert findings == [f"{IMAGES_MISSING_PREFIX} 1 of 1 provided content image(s) are not used in the generated HTML: catalogo.png"]


def test_icons_are_not_content_images() -> None:
    html = f'<html><head>{_STYLED}</head><body><img src="a/photo.jpg"></body></html>'
    srcs = ["a/photo.jpg", "a/icon-cart.png", "a/social_icons.svg", "a/favicon.ico", "a/sprite.png", "a/arrow.ico"]

    assert find_missing_content_images(html, _features(srcs)) == []


def test_logo_alt_text_does_not_exclude_a_content_image() -> None:
    features = FeatureModel(media=Media(images=[Image(src="a/insert.png", alt="blue geometric eye-shaped logo"), Image(src="a/drill.png", alt="Drill")]))
    html = '<html><body><img src="a/drill.png"></body></html>'

    findings = find_missing_content_images(html, features)

    assert findings == [f"{IMAGES_MISSING_PREFIX} 1 of 2 provided content image(s) are not used in the generated HTML: insert.png"]


def test_no_provided_images_gives_no_missing_images_finding() -> None:
    assert find_visual_defects(f"<html><head>{_STYLED}</head><body></body></html>", _features([])) == []


def test_styled_page_with_style_tag_has_no_unstyled_finding() -> None:
    html = "<html><head><style>body { color: red; }</style></head><body></body></html>"

    findings = find_visual_defects(html, _features([]))

    assert findings == []


def test_page_with_no_css_at_all_is_unstyled() -> None:
    html = "<html><head></head><body><p>hello</p></body></html>"

    findings = find_visual_defects(html, _features([]))

    assert any(finding.startswith("Unstyled:") for finding in findings)


def test_inline_only_styling_is_not_unstyled() -> None:
    html = '<html><body style="background:#fff;color:#111;margin:0"><p style="font-size:18px">hello</p></body></html>'

    assert _is_unstyled(html, None) is False
    assert _is_unstyled("<html><body><p>hello</p></body></html>", None) is True
    assert _is_unstyled('<html><body style=""><p style="color:red">hello</p></body></html>', None) is True
    assert _is_unstyled('<html><body><img src="a.png" style="width:100px;height:50px;border:0"></body></html>', None) is True


def test_browser_result_with_zero_rules_and_inline_styling_is_not_unstyled() -> None:
    html = '<html><body style="background:#fff;color:#111;margin:0"><p style="font-size:18px">hello</p></body></html>'
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, stylesheet_rule_count=0)],
    )

    assert _is_unstyled(html, browser_result) is False
    assert _is_unstyled('<html><body><img src="a.png" style="width:100px;height:50px;border:0"></body></html>', browser_result) is True


def test_linked_stylesheet_satisfies_the_static_check() -> None:
    html = '<html><head><link rel="stylesheet" href="styles.css"></head><body></body></html>'

    findings = find_visual_defects(html, _features([]))

    assert findings == []


def test_browser_result_with_zero_rules_everywhere_is_unstyled_despite_a_link_tag() -> None:
    html = '<html><head><link rel="stylesheet" href="broken.css"></head><body></body></html>'
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, stylesheet_rule_count=0),
            BrowserViewportResult(name="mobile", width=390, height=844, stylesheet_rule_count=0),
        ],
    )

    findings = find_visual_defects(html, _features([]), browser_result)

    assert any(finding.startswith("Unstyled:") for finding in findings)


def test_browser_result_with_nonzero_rules_is_not_unstyled() -> None:
    html = '<html><head><link rel="stylesheet" href="styles.css"></head><body></body></html>'
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, stylesheet_rule_count=42)],
    )

    findings = find_visual_defects(html, _features([]), browser_result)

    assert findings == []


_SCRIPT_ONLY_TAILWIND_HTML = '<html><head><script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script></head><body><p class="text-red-500">hello</p></body></html>'


def test_script_only_tailwind_page_with_runtime_rules_is_not_unstyled() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, stylesheet_rule_count=38),
            BrowserViewportResult(name="mobile", width=390, height=844, stylesheet_rule_count=38),
        ],
    )

    findings = find_visual_defects(_SCRIPT_ONLY_TAILWIND_HTML, _features([]), browser_result)

    assert not any(finding.startswith("Unstyled:") for finding in findings)


def test_script_only_tailwind_page_with_zero_runtime_rules_is_unstyled() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, stylesheet_rule_count=0),
            BrowserViewportResult(name="mobile", width=390, height=844, stylesheet_rule_count=0),
        ],
    )

    findings = find_visual_defects(_SCRIPT_ONLY_TAILWIND_HTML, _features([]), browser_result)

    assert any(finding.startswith("Unstyled:") for finding in findings)


def test_script_only_page_without_browser_result_keeps_the_static_verdict() -> None:
    findings = find_visual_defects(_SCRIPT_ONLY_TAILWIND_HTML, _features([]))

    assert any(finding.startswith("Unstyled:") for finding in findings)


def test_source_images_kept_has_no_finding() -> None:
    html = '<html><body><img src="../assets/logo.png"></body></html>'

    findings = find_visual_defects(html, _features(["assets/logo.png"]))

    assert not any(finding.startswith("Images dropped:") for finding in findings)


def test_source_images_all_dropped_is_reported() -> None:
    html = "<html><body><p>no images here</p></body></html>"

    findings = find_visual_defects(html, _features(["assets/logo.png", "assets/hero.jpg"]))

    dropped = [finding for finding in findings if finding.startswith("Images dropped:")]
    assert len(dropped) == 1
    assert "2" in dropped[0]


def test_no_source_images_means_no_finding() -> None:
    html = "<html><body><p>text only</p></body></html>"

    findings = find_visual_defects(html, _features([]))

    assert not any(finding.startswith("Images dropped:") for finding in findings)


def test_data_uri_images_are_not_counted_as_source_images() -> None:
    html = "<html><body><p>no images here</p></body></html>"

    findings = find_visual_defects(html, _features(["data:image/png;base64,abc123"]))

    assert not any(finding.startswith("Images dropped:") for finding in findings)


def test_reports_an_img_with_an_unterminated_src_value() -> None:
    html = '<img src/assets/bread-2.png" alt="크루아상">'

    findings = find_broken_image_markup(html)

    assert len(findings) == 1


def test_reports_an_img_with_a_missing_src() -> None:
    html = '<img alt="no source">'

    findings = find_broken_image_markup(html)

    assert len(findings) == 1


def test_accepts_an_img_with_only_a_srcset() -> None:
    html = '<img srcset="hero-800.png 800w, hero-1600.png 1600w" alt="hero">'

    assert find_broken_image_markup(html) == []


def test_accepts_an_img_with_a_normal_src() -> None:
    html = '<img src="assets/hero.png" alt="hero">'

    assert find_broken_image_markup(html) == []


def test_broken_img_markup_is_reported_through_find_visual_defects() -> None:
    html = '<html><body><style>body{color:red}</style><img src/assets/bread-2.png" alt="크루아상"></body></html>'

    findings = find_visual_defects(html, _features([]))

    broken = [finding for finding in findings if finding.startswith("Broken image:")]
    assert len(broken) == 1


def test_reports_exposed_markup_in_visible_text() -> None:
    html = '<html><body><style>body{color:red}</style><p>사업자등록번호 123-45-67890 font-size:14px"&gt;</p></body></html>'

    findings = find_exposed_markup(html)

    assert len(findings) == 1


def test_ignores_ordinary_prose_with_quotes() -> None:
    html = '<p>그는 "좋다"라고 말했다.</p>'

    assert find_exposed_markup(html) == []


def test_ignores_a_time_of_day() -> None:
    html = "<p>영업시간은 오전 10:30부터 입니다.</p>"

    assert find_exposed_markup(html) == []


def test_ignores_a_greater_than_comparison() -> None:
    html = "<p>a &gt; b 인 경우</p>"

    assert find_exposed_markup(html) == []


def test_ignores_exposed_markup_shapes_inside_code_blocks() -> None:
    html = '<pre><code>class="wrapper"&gt;</code></pre>'

    assert find_exposed_markup(html) == []


def test_overflowing_viewport_reports_width_and_offenders() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(
                name="mobile",
                width=390,
                height=844,
                scroll_width=470,
                viewport_width=390,
                overflow_elements=[OverflowElement(selector="section.hero", right=470)],
            ),
        ],
    )

    findings = find_overflow_defects(browser_result)

    assert len(findings) == 1
    assert findings[0].startswith("Horizontal overflow:")
    assert "mobile 390px: page is 470px wide (+80px)" in findings[0]
    assert "caused by section.hero" in findings[0]


def test_overflowing_viewport_reports_trapped_fixed_element_annotation() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(
                name="mobile",
                width=390,
                height=844,
                scroll_width=780,
                viewport_width=390,
                overflow_elements=[
                    OverflowElement(
                        selector="nav.nav-links (position: fixed trapped by header.site-header backdrop-filter)",
                        right=780,
                    ),
                ],
            ),
        ],
    )

    findings = find_overflow_defects(browser_result)

    assert len(findings) == 1
    assert "caused by nav.nav-links (position: fixed trapped by header.site-header backdrop-filter)" in findings[0]


def test_overflow_of_one_pixel_or_less_is_ignored() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="mobile", width=390, height=844, scroll_width=391, viewport_width=390),
        ],
    )

    assert find_overflow_defects(browser_result) == []


def test_multiple_overflowing_viewports_are_all_reported() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="tablet", width=768, height=1024, scroll_width=800, viewport_width=768),
            BrowserViewportResult(name="mobile", width=390, height=844, scroll_width=470, viewport_width=390),
        ],
    )

    findings = find_overflow_defects(browser_result)

    assert len(findings) == 1
    assert "tablet 768px: page is 800px wide (+32px)" in findings[0]
    assert "mobile 390px: page is 470px wide (+80px)" in findings[0]


def test_overflow_finding_is_wired_into_find_visual_defects() -> None:
    html = "<html><head><style>body { color: red; }</style></head><body></body></html>"
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="mobile", width=390, height=844, scroll_width=470, viewport_width=390)],
    )

    findings = find_visual_defects(html, _features([]), browser_result)

    assert any(finding.startswith("Horizontal overflow:") for finding in findings)


def test_a_narrow_text_median_is_reported() -> None:
    findings = find_wide_text_width_defects(BrowserEvaluationResult(enabled=True, wide_text_width_median=332))

    assert len(findings) == 1
    assert findings[0].startswith(WIDE_TEXT_WIDTH_PREFIX)
    assert "332px" in findings[0]
    assert "1920px" in findings[0]
    assert "cap" not in findings[0]


def test_a_wide_text_median_is_not_reported() -> None:
    assert find_wide_text_width_defects(BrowserEvaluationResult(enabled=True, wide_text_width_median=600)) == []
    assert find_wide_text_width_defects(BrowserEvaluationResult(enabled=True, wide_text_width_median=1018)) == []


def test_an_unmeasured_text_width_is_not_reported() -> None:
    assert find_wide_text_width_defects(BrowserEvaluationResult(enabled=True)) == []
    assert find_wide_text_width_defects(None) == []
    assert find_wide_text_width_defects(BrowserEvaluationResult(enabled=False, wide_text_width_median=332)) == []


def test_the_narrow_text_finding_is_wired_into_find_visual_defects() -> None:
    html = "<html><head><style>body { color: red; }</style></head><body></body></html>"

    findings = find_visual_defects(html, _features([]), BrowserEvaluationResult(enabled=True, wide_text_width_median=332))

    assert any(finding.startswith(WIDE_TEXT_WIDTH_PREFIX) for finding in findings)
