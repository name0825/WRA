"""A candidate is blamed only for what it lost or broke relative to the page it came from."""

from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
from wra.core.models import FeatureModel
from wra.validator.regression import find_regressions

PHONE = "02-1234-5678"
MAIL = "mailto:hello@example.com"
SITE = "https://example.com/shop"

FEATURES = FeatureModel(
    business={"phone": [PHONE]},
    content={"links": [{"text": "메일", "href": MAIL}, {"text": "상점", "href": SITE}, {"text": "위로", "href": "#top"}]},
    media={"images": [{"src": "assets/a.jpg"}, {"src": "assets/b.jpg"}, {"src": "assets/c.jpg"}]},
)

BASE = (
    f'<html><body><p>전화 {PHONE}</p><a href="{MAIL}">메일</a><a href="{SITE}/">상점</a><a href="#top">위로</a>'
    '<img src="assets/a.jpg"><img src="assets/b.jpg"><img src="assets/c.jpg"></body></html>'
)


def _browser(width: int = 1280, scroll: int = 1280, broken=(), errors=(), name: str = "desktop") -> BrowserEvaluationResult:
    viewport = BrowserViewportResult(name=name, width=width, height=800, viewport_width=width, scroll_width=scroll, broken_images=list(broken), page_errors=list(errors))
    return BrowserEvaluationResult(enabled=True, viewports=[viewport])


def _check(html: str, base_html: str = BASE, base_browser=None, browser=None, features: FeatureModel = FEATURES, brief: bool = True) -> list[str]:
    return find_regressions(base_html, base_browser, html, browser, features, check_brief_facts=brief)


def test_a_lost_brief_fact_is_flagged() -> None:
    found = _check(BASE.replace(PHONE, ""))
    assert len(found) == 1 and found[0].startswith("regression:") and PHONE in found[0]


def test_brief_facts_are_ignored_unless_asked_for() -> None:
    assert _check(BASE.replace(PHONE, ""), brief=False) == []


def test_a_fact_already_missing_in_the_base_is_not_blamed_on_the_candidate() -> None:
    without = BASE.replace(PHONE, "")
    assert _check(without, base_html=without) == []


def test_a_lost_mailto_or_external_link_is_flagged() -> None:
    assert any(MAIL in item for item in _check(BASE.replace(MAIL, "#")))
    assert any(SITE in item for item in _check(BASE.replace(f"{SITE}/", "#")))


def test_a_new_bracketed_placeholder_is_flagged() -> None:
    found = _check(BASE.replace("</body>", "<p>[Visual Detail]</p></body>"))
    assert len(found) == 1 and "[Visual Detail]" in found[0]


def test_a_placeholder_already_in_the_base_or_the_source_is_not_flagged() -> None:
    marked = BASE.replace("</body>", "<p>[Visual Detail]</p></body>")
    assert _check(marked, base_html=marked) == []
    sourced = FeatureModel(**{**FEATURES.model_dump(), "content": {"text": ["[Visual Detail]"], "links": FEATURES.model_dump()["content"]["links"]}})
    assert _check(marked, features=sourced) == []


def test_dropping_every_source_image_is_flagged() -> None:
    html = BASE.replace("assets/a.jpg", "x").replace("assets/b.jpg", "y").replace("assets/c.jpg", "z")
    found = _check(html)
    assert len(found) == 1 and "image" in found[0]


def test_omitting_some_images_is_not_flagged() -> None:
    assert _check(BASE.replace('<img src="assets/b.jpg">', "").replace('<img src="assets/c.jpg">', "")) == []


def test_a_reordered_page_with_the_same_facts_and_links_is_not_flagged() -> None:
    reordered = (
        f'<html><body><footer><a href="{SITE}">상점</a><a href="{MAIL}">메일</a></footer><img src="assets/c.jpg">'
        f"<section><h2>연락</h2><p>전화 {PHONE}</p></section></body></html>"
    )
    assert _check(reordered) == []


def test_a_reworded_fact_that_is_still_shown_is_not_flagged() -> None:
    assert _check(BASE.replace(f"전화 {PHONE}", f"문의는 {PHONE} 으로")) == []


def test_removing_an_internal_anchor_is_not_flagged() -> None:
    assert _check(BASE.replace('<a href="#top">위로</a>', "")) == []


def test_new_browser_defects_are_flagged() -> None:
    clean = _browser()
    assert any("broken image" in item for item in _check(BASE, base_browser=clean, browser=_browser(broken=["assets/a.jpg"])))
    assert any("overflow" in item for item in _check(BASE, base_browser=clean, browser=_browser(scroll=1500)))
    assert any("script error" in item for item in _check(BASE, base_browser=clean, browser=_browser(errors=["boom"])))


def test_browser_defects_already_in_the_base_are_not_blamed_on_the_candidate() -> None:
    dirty = _browser(scroll=1500, broken=["assets/a.jpg"], errors=["boom"])
    assert _check(BASE, base_browser=dirty, browser=_browser(scroll=1600, broken=["assets/a.jpg"], errors=["boom"])) == []


def test_browser_checks_are_skipped_without_both_results() -> None:
    bad = _browser(scroll=1500, broken=["assets/a.jpg"], errors=["boom"])
    assert _check(BASE, base_browser=None, browser=bad) == []
    assert _check(BASE, base_browser=_browser(), browser=None) == []
    assert _check(BASE, base_browser=BrowserEvaluationResult(enabled=False), browser=bad) == []


def _linked(href: str, source: str) -> list[str]:
    features = FeatureModel(content={"links": [{"text": "x", "href": source}]})
    return find_regressions(f'<a href="{source}">x</a>', None, f'<a href="{href}">x</a>', None, features)


def test_equivalent_spellings_of_a_source_link_are_not_regressions() -> None:
    assert _linked("tel:01012345678", "tel:010-1234-5678") == []
    assert _linked("tel:+82-10-1234-5678", "tel:+821012345678") == []
    assert _linked("mailto:A@B.com?subject=x", "mailto:a@b.com") == []
    assert _linked("https://example.com/x", "HTTPS://Example.com/x") == []
    assert _linked("https://example.com/p?a=1&amp;b=2", "https://example.com/p?a=1&b=2") == []


def test_a_genuinely_removed_or_changed_link_is_still_a_regression() -> None:
    assert _linked("#top", "https://example.com/x")
    assert _linked("tel:01099999999", "tel:010-1234-5678")
    assert _linked("https://example.com/y", "https://example.com/x")


def test_a_bracket_label_whose_text_is_in_the_source_is_not_flagged() -> None:
    features = FeatureModel(content={"text": ["Read more about us"]})
    assert find_regressions("<p>hi</p>", None, "<p>[Read more]</p>", None, features) == []
    assert find_regressions("<p>hi</p>", None, "<p>[Visual Detail]</p>", None, features)


def test_a_meta_note_is_compared_whole_against_the_normalized_source() -> None:
    note = "(source: 회사 소개서)"
    features = FeatureModel(content={"text": [f"문서 {note}"]})
    assert find_regressions("<p>hi</p>", None, f"<p>{note}</p>", None, features) == []
    assert find_regressions("<p>hi</p>", None, "<p>(source: 다른 문서)</p>", None, features)
    assert find_regressions("<p>hi</p>", None, "<p>（source: 다른 문서）</p>", None, features)


def test_a_viewport_missing_from_the_base_is_not_blamed_for_overflow() -> None:
    assert _check(BASE, base_browser=_browser(name="desktop"), browser=_browser(scroll=1500, name="mobile")) == []


def test_a_bracket_label_is_matched_as_a_whole_word_in_the_source() -> None:
    features = FeatureModel(content={"text": ["images and photography of Mapo"]})
    for label in ("Image", "Photo", "Map"):
        assert find_regressions("<p>hi</p>", None, f"<p>[{label}]</p>", None, features), label
    features = FeatureModel(content={"text": ["Read more"]})
    assert find_regressions("<p>hi</p>", None, "<p>[Read more]</p>", None, features) == []
