from wra.core.models import Content, FeatureModel, SourceAssets, StyleDigest
from wra.validator.craft import find_craft_issues, find_refinable_craft_defects


def _rules(html: str, features: FeatureModel | None = None) -> set[str]:
    return {issue.rule for issue in find_craft_issues(html, features)}


def _features(paragraphs: list[str] | None = None, palette: list[str] | None = None) -> FeatureModel:
    return FeatureModel(
        content=Content(paragraphs=paragraphs or []),
        source_assets=SourceAssets(styles=StyleDigest(palette=palette or [])),
    )


def _style(css: str) -> str:
    return f"<html><head><style>{css}</style></head><body></body></html>"


def test_transition_all_positive_and_negative() -> None:
    assert "transition-all" in _rules(_style("a { transition: all .3s ease; }"))
    assert "transition-all" not in _rules(_style("a { transition: color .3s ease; }"))


def test_outline_removed_positive_and_negative() -> None:
    assert "outline-removed" in _rules(_style("button { outline: none; }"))
    assert "outline-removed" in _rules(_style("button { outline: 0; }"))
    assert "outline-removed" not in _rules(_style("button { outline: none; } button:focus-visible { outline: 2px solid red; }"))
    assert "outline-removed" not in _rules(_style("button { outline: 0.5px solid red; }"))


def test_zoom_disabled_positive_and_negative() -> None:
    assert "zoom-disabled" in _rules('<meta name="viewport" content="width=device-width, user-scalable=no">')
    assert "zoom-disabled" in _rules('<meta name="viewport" content="width=device-width, maximum-scale=1">')
    assert "zoom-disabled" not in _rules('<meta name="viewport" content="width=device-width, initial-scale=1">')


def test_img_no_dimensions_positive_and_negative() -> None:
    assert "img-no-dimensions" in _rules('<img src="a.png" width="10">')
    assert "img-no-dimensions" not in _rules('<img src="a.png" width="10" height="10">')
    assert "img-no-dimensions" not in _rules('<img src="a.png" style="aspect-ratio: 4 / 3">')
    assert "img-no-dimensions" not in _rules('<style>img { aspect-ratio: 1; }</style><img src="a.png">')


def test_clickable_non_control_positive_and_negative() -> None:
    assert "clickable-non-control" in _rules('<div onclick="go()">x</div>')
    assert "clickable-non-control" in _rules('<span role="button">x</span>')
    assert "clickable-non-control" not in _rules('<span role="button" tabindex="0">x</span>')
    assert "clickable-non-control" not in _rules('<button onclick="go()">x</button>')


def test_emoji_icon_positive_negative_and_source_exemption() -> None:
    html = "<button>\U0001F680 Launch</button>"
    assert "emoji-icon" in _rules(html)
    assert "emoji-icon" not in _rules("<button>Launch</button>")
    assert "emoji-icon" not in _rules("<p>\U0001F680 Launch</p>")
    assert "emoji-icon" not in _rules(html, _features(paragraphs=["Ready \U0001F680"]))


def test_tailwind_violet_positive_negative_and_palette_exemption() -> None:
    css = "a { color: #6366F1; }"
    assert "tailwind-violet" in _rules(_style(css))
    assert "tailwind-violet" not in _rules(_style("a { color: #123456; }"))
    assert "tailwind-violet" not in _rules(_style(css), _features(palette=["--brand: #6366f1"]))
    assert "tailwind-violet" not in _rules(_style("a { color: #63f; }"), _features(palette=["#6633ff x3"]))


def test_purple_blue_gradient_positive_negative_and_palette_exemption() -> None:
    css = "div { background: linear-gradient(135deg, #8000ff, #0066ff); }"
    assert "purple-blue-gradient" in _rules(_style(css))
    assert "purple-blue-gradient" in _rules(_style("div { background: radial-gradient(rgb(128, 0, 255), rgba(0, 102, 255, .5)); }"))
    assert "purple-blue-gradient" not in _rules(_style("div { background: linear-gradient(#ff0000, #0066ff); }"))
    assert "purple-blue-gradient" not in _rules(_style("div { background: linear-gradient(#8000ff, #ff0000); }"))
    assert "purple-blue-gradient" not in _rules(_style(css), _features(palette=["#8000ff x2"]))


def _sprawl_css(count: int) -> str:
    return " ".join(f".c{i} {{ color: #{i:02x}{i:02x}{i:02x}; }}" for i in range(1, count + 1))


def test_hex_sprawl_positive_negative_root_and_palette_exemption() -> None:
    assert "hex-sprawl" in _rules(_style(_sprawl_css(13)))
    assert "hex-sprawl" not in _rules(_style(_sprawl_css(12)))
    root_css = ":root { " + " ".join(f"--c{i}: #{i:02x}{i:02x}{i:02x};" for i in range(1, 20)) + " }"
    assert "hex-sprawl" not in _rules(_style(root_css))
    palette = [f"#{i:02x}{i:02x}{i:02x}" for i in range(1, 4)]
    assert "hex-sprawl" not in _rules(_style(_sprawl_css(13)), _features(palette=palette))


def test_hex_sprawl_ignores_hex_looking_selectors_and_nested_media() -> None:
    css = "#add, #fade { margin: 0; } @media (min-width: 600px) { :root { --a: #111111; --b: #222222; } }"
    assert "hex-sprawl" not in _rules(_style(css + _sprawl_css(12)))


def test_heavy_blur_positive_and_negative() -> None:
    assert "heavy-blur" in _rules(_style("div { backdrop-filter: blur(20px); }"))
    assert "heavy-blur" in _rules(_style("div { filter: blur(1rem); }"))
    assert "heavy-blur" not in _rules(_style("div { backdrop-filter: blur(8px); }"))
    assert "heavy-blur" not in _rules(_style("div { filter: blur(0.25rem); }"))


def test_placeholder_copy_positive_negative_and_source_exemption() -> None:
    assert "placeholder-copy" in _rules("<p>Lorem ipsum dolor</p>")
    assert "placeholder-copy" in _rules("<p>Welcome to Acme Corp</p>")
    assert "placeholder-copy" not in _rules("<p>Welcome to the acme of design</p>")
    assert "placeholder-copy" not in _rules("<script>var s = 'John Doe';</script><p>Hello</p>")
    assert "placeholder-copy" not in _rules("<p>Welcome to Acme Corp</p>", _features(paragraphs=["Acme Corp since 1990"]))


def test_arrow_suffix_positive_negative_and_source_exemption() -> None:
    assert "arrow-suffix" in _rules('<a href="#">Learn more &rarr;</a>')
    assert "arrow-suffix" in _rules("<button>Go -&gt;</button>")
    assert "arrow-suffix" not in _rules('<a href="#">Learn more</a>')
    assert "arrow-suffix" not in _rules("<p>Learn more →</p>")
    assert "arrow-suffix" not in _rules('<a href="#">Learn more →</a>', _features(paragraphs=["Learn more →"]))


def test_middot_meta_positive_negative_and_source_exemption() -> None:
    assert "middot-meta" in _rules("<p>Design · Build · Ship</p>")
    assert "middot-meta" in _rules("<p>Design • Build • Ship</p>")
    assert "middot-meta" not in _rules("<p>Design · Build</p>")
    assert "middot-meta" not in _rules("<p>Design · Build · Ship</p>", _features(paragraphs=["Design · Build · Ship"]))


def test_issues_are_capped_per_rule_and_deduplicated() -> None:
    html = "".join(f'<div onclick="f{i}()">x</div>' for i in range(9)) + '<div onclick="f0()">x</div>'
    issues = [issue for issue in find_craft_issues(html) if issue.rule == "clickable-non-control"]
    assert len(issues) == 1
    html = "".join(f'<img src="{i}.png">' for i in range(9))
    assert len([i for i in find_craft_issues(html) if i.rule == "img-no-dimensions"]) == 5


def test_categories_and_clean_page() -> None:
    issues = find_craft_issues(_style("a { transition: all 1s; color: #6366f1; }"))
    assert {(i.rule, i.category) for i in issues} == {("transition-all", "quality"), ("tailwind-violet", "ai_tell")}
    assert find_craft_issues("<html><body><p>Hello</p></body></html>") == []


def test_img_sized_by_css_height_or_absolute_position_is_not_flagged() -> None:
    assert "img-no-dimensions" not in _rules('<img src="a.png" style="height: 200px">')
    assert "img-no-dimensions" not in _rules('<img src="a.png" style="position: absolute">')
    assert "img-no-dimensions" not in _rules('<style>img { height: 100%; }</style><img src="a.png">')
    assert "img-no-dimensions" not in _rules('<style>.card .hero { height: 12rem; }</style><img class="x hero" src="a.png">')
    assert "img-no-dimensions" not in _rules('<style>#logo:hover { position: fixed; }</style><img id="logo" src="a.png">')
    assert "img-no-dimensions" in _rules('<style>img { height: auto; }</style><img src="a.png">')
    assert "img-no-dimensions" in _rules('<style>.other { height: 12rem; } .box img.x { color: red; }</style><img src="a.png">')


def test_emoji_icon_only_flags_emoji_presentation_characters() -> None:
    for glyph in ("\u2630", "\u2713", "\u2605", "\u2715", "\u00d7", "\u203a"):
        assert "emoji-icon" not in _rules(f"<button>{glyph}</button>")
    for glyph in ("\u2705", "\u274c", "\u2b50", "\u26a1", "\u2728", "\u2764", "\u260e", "\u2709", "\u26a0"):
        assert "emoji-icon" in _rules(f"<a href=\"#\">{glyph} x</a>")


def test_hex_inside_url_is_ignored() -> None:
    css = "".join(f".c{i} {{ fill: url(#fade{i}); }}" for i in range(20)) + ".d { filter: url(#abc); }"
    assert "hex-sprawl" not in _rules(_style(css))
    assert "tailwind-violet" not in _rules(_style(".a { fill: url(#818cf8); }"))
    assert "tailwind-violet" in _rules(_style(".a { fill: url(#x); color: #818cf8; }"))


def test_rgb_and_hsl_palette_entries_exempt_source_colors() -> None:
    css = "a { color: #6366f1; }"
    assert "tailwind-violet" not in _rules(_style(css), _features(palette=["rgb(99, 102, 241)"]))
    assert "tailwind-violet" not in _rules(_style(css), _features(palette=["rgba(99,102,241,0.5)"]))
    assert "tailwind-violet" not in _rules(_style("a { color: #8b5cf6; }"), _features(palette=["hsl(258.3, 89.5%, 66.3%)"]))
    assert "tailwind-violet" in _rules(_style(css), _features(palette=["rgb(1, 2, 3)"]))


def test_malformed_hsl_palette_entries_do_not_raise() -> None:
    for entry in ("hsl(.,50%,50%)", "hsl(1.2.3 50% 50%)"):
        assert "tailwind-violet" in _rules(_style("a { color: #6366f1; }"), _features(palette=[entry]))


def test_refinable_craft_defects_return_fixable_rules_only() -> None:
    html = _style("a { transition: all .2s; } a:hover { outline: none; } div { backdrop-filter: blur(20px); }")
    defects = find_refinable_craft_defects(html)
    assert len(defects) == 2
    assert any(defect.startswith("transition: all .2s: list only the properties") for defect in defects)
    assert any(defect.startswith("outline: none: the page removes the focus outline") for defect in defects)
    assert not any("blur" in defect for defect in defects)
