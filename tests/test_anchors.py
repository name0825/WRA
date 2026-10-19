from wra.validator.anchors import find_broken_anchors, repair_broken_anchors, repair_doubled_url_schemes


def test_reports_a_broken_anchor() -> None:
    html = """
    <html><body>
    <nav><a href="#about">About</a><a href="#contact">Contact</a></nav>
    <section id="home"></section>
    <section id="contact"></section>
    </body></html>
    """

    findings = find_broken_anchors(html)

    assert len(findings) == 1
    assert "#about" in findings[0]


def test_accepts_a_page_whose_anchors_all_resolve() -> None:
    html = """
    <html><body>
    <nav><a href="#home">Home</a><a href="#contact">Contact</a></nav>
    <section id="home"></section>
    <section id="contact"></section>
    </body></html>
    """

    assert find_broken_anchors(html) == []


def test_skips_spa_style_bang_fragments() -> None:
    html = '<a href="#!">Toggle</a>'

    assert find_broken_anchors(html) == []


def test_skips_the_top_fragment_in_any_case() -> None:
    html = '<a href="#top">Back to top</a><a href="#TOP">Top</a>'

    assert find_broken_anchors(html) == []


def test_skips_spa_style_route_fragments() -> None:
    html = '<a href="#/dashboard">Dashboard</a>'

    assert find_broken_anchors(html) == []


def test_matches_a_percent_encoded_fragment_to_its_decoded_id() -> None:
    html = """
    <html><body>
    <a href="#%ED%95%9C">Link</a>
    <section id="한"></section>
    </body></html>
    """

    assert find_broken_anchors(html) == []


def test_repairs_broken_anchors_to_their_unambiguous_matching_ids() -> None:
    html = """
    <nav><a href="#pricing">Pricing</a><a href="#features">Features</a><a href="#faq">FAQ</a></nav>
    <section id="hero"></section>
    <section id="specimen_preview"></section>
    <section id="features_grid"></section>
    <section id="pricing_tiers"></section>
    <section id="social_proof"></section>
    <section id="faq_section"></section>
    <section id="footer"></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert find_broken_anchors(repaired) == []
    assert applied == ["#pricing -> #pricing_tiers", "#features -> #features_grid", "#faq -> #faq_section"]


def test_repairs_what_it_can_and_leaves_an_unmatched_anchor_broken() -> None:
    html = """
    <nav><a href="#pricing">Pricing</a><a href="#faq">FAQ</a><a href="#contact">Contact</a></nav>
    <section id="hero_command"></section>
    <section id="capabilities_matrix"></section>
    <section id="pricing_structure"></section>
    <section id="social_proof"></section>
    <section id="faq_section"></section>
    <section id="footer"></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert applied == ["#pricing -> #pricing_structure", "#faq -> #faq_section"]
    remaining = find_broken_anchors(repaired)
    assert len(remaining) == 1
    assert "#contact" in remaining[0]


def test_leaves_an_anchor_unchanged_when_no_id_matches() -> None:
    html = """
    <nav><a href="#location">Location</a></nav>
    <section id="hero"></section>
    <section id="intro"></section>
    <section id="menu"></section>
    <section id="hours"></section>
    <section id="notice"></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert applied == []
    assert repaired == html


def test_leaves_home_anchor_unchanged_when_no_id_matches() -> None:
    html = """
    <nav><a href="#home">Home</a></nav>
    <section id="hero"></section>
    <section id="expertise"></section>
    <section id="ledger"></section>
    <section id="contact"></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert applied == []
    assert repaired == html


def test_leaves_an_ambiguous_anchor_unchanged() -> None:
    html = """
    <nav><a href="#pricing">Pricing</a></nav>
    <section id="pricing_tiers"></section>
    <section id="pricing_plan"></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert applied == []
    assert repaired == html


def test_does_not_rewrite_the_top_fragment() -> None:
    html = '<a href="#top">Back to top</a><section id="top_banner"></section>'

    repaired, applied = repair_broken_anchors(html)

    assert applied == []
    assert repaired == html


def test_does_not_touch_non_href_occurrences_of_a_broken_target() -> None:
    html = """
    <nav><a href="#pricing">Pricing</a></nav>
    <section id="pricing_tiers"><p>See #pricing for details</p></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert applied == ["#pricing -> #pricing_tiers"]
    assert '<a href="#pricing_tiers">Pricing</a>' in repaired
    assert "<p>See #pricing for details</p>" in repaired


def test_rewrites_only_the_href_of_anchor_elements() -> None:
    html = """
    <nav><a class="nav" href="#pricing">Pricing</a></nav>
    <div data-href="#pricing"></div>
    <svg><use href="#pricing"></use></svg>
    <section id="pricing_tiers"></section>
    """

    repaired, applied = repair_broken_anchors(html)

    assert applied == ["#pricing -> #pricing_tiers"]
    assert '<a class="nav" href="#pricing_tiers">' in repaired
    assert '<div data-href="#pricing">' in repaired
    assert '<use href="#pricing">' in repaired


def test_repairs_a_doubled_github_url() -> None:
    html = '<a href="https://github.com/https://github.com/name0825">GitHub</a>'

    repaired, applied = repair_doubled_url_schemes(html)

    assert repaired == '<a href="https://github.com/name0825">GitHub</a>'
    assert applied == ["https://github.com/https://github.com/name0825 -> https://github.com/name0825"]


def test_repairs_a_bare_doubled_scheme() -> None:
    html = '<a href="https://https://x.com">Link</a>'

    repaired, applied = repair_doubled_url_schemes(html)

    assert repaired == '<a href="https://x.com">Link</a>'
    assert applied == ["https://https://x.com -> https://x.com"]


def test_repairs_a_tripled_doubled_scheme() -> None:
    html = '<a href="https://https://https://x.com">Link</a>'

    repaired, applied = repair_doubled_url_schemes(html)

    assert repaired == '<a href="https://x.com">Link</a>'
    assert applied == ["https://https://https://x.com -> https://x.com"]


def test_leaves_an_archive_org_wrapper_url_untouched() -> None:
    html = '<a href="https://web.archive.org/web/2020/https://example.com">Archive</a>'

    repaired, applied = repair_doubled_url_schemes(html)

    assert applied == []
    assert repaired == html


def test_repairs_a_doubled_url_in_visible_text() -> None:
    html = '<a href="https://github.com/name0825">https://github.com/https://github.com/name0825</a>'

    repaired, applied = repair_doubled_url_schemes(html)

    assert repaired == '<a href="https://github.com/name0825">https://github.com/name0825</a>'
    assert applied == ["https://github.com/https://github.com/name0825 -> https://github.com/name0825"]


def test_leaves_a_doubled_url_inside_an_unclosed_script_untouched() -> None:
    html = '<script>var u="https://github.com/https://github.com/x";'

    repaired, applied = repair_doubled_url_schemes(html)

    assert applied == []
    assert repaired == html


def test_leaves_a_doubled_url_inside_script_text_untouched() -> None:
    html = '<script>var url = "https://github.com/https://github.com/name0825";</script>'

    repaired, applied = repair_doubled_url_schemes(html)

    assert applied == []
    assert repaired == html


def test_leaves_an_archive_org_wrapper_url_in_text_untouched() -> None:
    html = "<p>https://web.archive.org/web/2020/https://example.com</p>"

    repaired, applied = repair_doubled_url_schemes(html)

    assert applied == []
    assert repaired == html


def test_repairs_a_doubled_scheme_in_a_single_quoted_attribute() -> None:
    html = "<a href='https://github.com/https://github.com/name0825'>GitHub</a>"

    repaired, applied = repair_doubled_url_schemes(html)

    assert repaired == "<a href='https://github.com/name0825'>GitHub</a>"
    assert applied == ["https://github.com/https://github.com/name0825 -> https://github.com/name0825"]


def test_leaves_html_with_no_doubled_urls_unchanged() -> None:
    html = '<a href="https://github.com/name0825">GitHub</a><img src="https://example.com/logo.png">'

    repaired, applied = repair_doubled_url_schemes(html)

    assert applied == []
    assert repaired == html
