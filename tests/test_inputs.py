from pathlib import Path

import pytest

from wra.analyzer.assets import build_asset_infos, build_script_digest, build_style_digest, image_dimensions
from wra.core.inputs import InputError, collect_css, collect_js, copy_assets, discover, page_map_for, rewrite_asset_paths

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _png(path: Path, width: int, height: int, padding: int = 0) -> None:
    data = bytearray(PNG_1x1)
    data[16:20] = width.to_bytes(4, "big")
    data[20:24] = height.to_bytes(4, "big")
    path.write_bytes(bytes(data) + b"\0" * padding)


def _site(tmp_path: Path) -> Path:
    root = tmp_path / "input"
    (root / "src" / "img").mkdir(parents=True)
    (root / "css").mkdir()
    (root / "js").mkdir()
    (root / "index.html").write_text(
        '<html><head><title>Home</title><link rel="stylesheet" href="/css/site.css"></head>'
        '<body><h1>Home</h1><img src="/src/img/hero.png" alt="Hero">'
        '<a href="about.html">About</a><script src="/js/site.js"></script></body></html>',
        encoding="utf-8",
    )
    (root / "about.html").write_text(
        '<html><head><title>About</title></head><body><h1>About</h1></body></html>',
        encoding="utf-8",
    )
    (root / "_draft.html").write_text("<html><body>draft</body></html>", encoding="utf-8")
    (root / "css" / "site.css").write_text(
        ":root{--navy:#2B4884;--font-body:'Outfit',sans-serif}"
        "body{font-family:var(--font-body);font-size:1rem;padding:2rem}"
        ".card{border-radius:8px;display:grid;background:url('/src/img/hero.png')}"
        "@media (max-width: 768px){.card{padding:1rem}}",
        encoding="utf-8",
    )
    (root / "js" / "site.js").write_text(
        "document.addEventListener('click', () => document.body.classList.toggle('modal-open'));"
        "new Swiper('.slider');",
        encoding="utf-8",
    )
    _png(root / "src" / "img" / "hero.png", 1600, 900, padding=8000)
    _png(root / "src" / "img" / "icon.png", 32, 32)
    return root


def test_discover_selects_index_and_ignores_underscored(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    assert bundle.entry.relative == "index.html"
    assert [page.relative for page in bundle.pages] == ["index.html", "about.html"]


def test_discover_requires_html(tmp_path: Path) -> None:
    empty = tmp_path / "input"
    empty.mkdir()
    with pytest.raises(InputError):
        discover(empty)


def test_asset_map_covers_media_only(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    # Keyed by source path and by basename: the basename joins markup that
    # addresses a path we never received, the source path tells two files of
    # the same name apart.
    assert bundle.asset_map == {
        "hero.png": "assets/hero.png",
        "icon.png": "assets/icon.png",
        "src/img/hero.png": "assets/hero.png",
        "src/img/icon.png": "assets/icon.png",
    }


def test_duplicate_basenames_keep_separate_files(tmp_path: Path) -> None:
    root = tmp_path / "input"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "index.html").write_text(
        '<html><body><img src="a/logo.png"><img src="b/logo.png"></body></html>',
        encoding="utf-8",
    )
    _png(root / "a" / "logo.png", 100, 50)
    _png(root / "b" / "logo.png", 200, 80)

    bundle = discover(root)
    html = rewrite_asset_paths(bundle.entry.html, bundle.render_map)
    assert 'src="assets/logo.png"' in html
    assert 'src="assets/b/logo.png"' in html

    run_dir = tmp_path / "run"
    assert copy_assets(bundle, run_dir) == 2
    # Both files survive, each at the path the rewritten markup points to.
    assert (run_dir / "assets" / "logo.png").read_bytes() == (root / "a" / "logo.png").read_bytes()
    assert (run_dir / "assets" / "b" / "logo.png").read_bytes() == (root / "b" / "logo.png").read_bytes()
    assert [(info.path, info.width) for info in build_asset_infos(bundle)] == [
        ("assets/logo.png", 100),
        ("assets/b/logo.png", 200),
    ]


def test_duplicate_basename_at_the_input_root_keeps_both_files(tmp_path: Path) -> None:
    """A file at the input root has no directory to be kept apart by: its
    source path is its basename. Keying it at `logo.png` used to take the key
    the deeper file's basename alias already held and map both to
    `assets/logo.png`, so one file overwrote the other on copy and both
    references rendered the same image."""
    root = tmp_path / "input"
    (root / "a").mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><body><img src="a/logo.png"><img src="logo.png"></body></html>',
        encoding="utf-8",
    )
    _png(root / "a" / "logo.png", 100, 50)
    _png(root / "logo.png", 200, 80)

    bundle = discover(root)
    html = rewrite_asset_paths(bundle.entry.html, bundle.render_map)
    assert 'src="assets/logo.png"' in html
    assert 'src="assets/logo-2.png"' in html

    run_dir = tmp_path / "run"
    assert copy_assets(bundle, run_dir) == 2
    assert (run_dir / "assets" / "logo.png").read_bytes() == (root / "a" / "logo.png").read_bytes()
    assert (run_dir / "assets" / "logo-2.png").read_bytes() == (root / "logo.png").read_bytes()
    assert sorted((info.path, info.width) for info in build_asset_infos(bundle)) == [
        ("assets/logo-2.png", 200),
        ("assets/logo.png", 100),
    ]


def test_rewrite_asset_paths_normalises_absolute_references(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    html = rewrite_asset_paths(bundle.entry.html, bundle.asset_map, page_map_for(bundle))
    assert 'src="assets/hero.png"' in html
    assert 'href="about.html"' in html
    assert "/src/img/hero.png" not in html


def test_rewrite_leaves_remote_references_untouched(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    html = rewrite_asset_paths('<img src="https://cdn.example.com/hero.png">', bundle.asset_map)
    assert html == '<img src="https://cdn.example.com/hero.png">'


def test_copy_assets_delivers_everything_the_source_page_renders_with(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    run_dir = tmp_path / "run"
    assert copy_assets(bundle, run_dir) == 4
    for name in ("hero.png", "icon.png", "site.css", "site.js"):
        assert (run_dir / "assets" / name).is_file()


def test_source_page_keeps_its_stylesheet_while_the_inventory_does_not(tmp_path: Path) -> None:
    """The screenshot must show the real site, but the redesign may not relink its CSS."""
    bundle = discover(_site(tmp_path))
    html = rewrite_asset_paths(bundle.entry.html, bundle.render_map, page_map_for(bundle))
    assert 'href="assets/site.css"' in html
    assert 'src="assets/site.js"' in html
    assert "site.css" not in bundle.asset_map


def test_collect_css_and_js_follow_local_references(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    css = collect_css(bundle, bundle.entry, 512000)
    js = collect_js(bundle, bundle.entry, 512000)
    assert "--navy" in css
    assert "Swiper" in js


def test_collect_css_respects_the_byte_cap(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    assert len(collect_css(bundle, bundle.entry, 40)) <= 40


def test_collect_css_rejects_a_path_traversal_reference(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("SECRET_TOKEN", encoding="utf-8")
    root = tmp_path / "input"
    root.mkdir()
    (root / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="../secret.txt"></head><body></body></html>',
        encoding="utf-8",
    )
    bundle = discover(root)
    css = collect_css(bundle, bundle.entry, 512000)
    assert "SECRET_TOKEN" not in css


def test_collect_js_rejects_a_drive_letter_absolute_reference(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    (root / "index.html").write_text(
        '<html><body><script src="C:/Windows/System32/drivers/etc/hosts"></script></body></html>',
        encoding="utf-8",
    )
    bundle = discover(root)
    js = collect_js(bundle, bundle.entry, 512000)
    assert js == ""


def test_collect_js_follows_a_backslash_reference(tmp_path: Path) -> None:
    root = tmp_path / "input"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><body><script src="js\\site.js"></script></body></html>',
        encoding="utf-8",
    )
    (root / "js" / "site.js").write_text("new Swiper('.slider');", encoding="utf-8")
    bundle = discover(root)
    js = collect_js(bundle, bundle.entry, 512000)
    assert "Swiper" in js


def test_style_digest_resolves_custom_properties(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    digest = build_style_digest(collect_css(bundle, bundle.entry, 512000))
    assert "--navy: #2B4884" in digest.palette
    assert "--font-body: Outfit" in digest.fonts
    assert digest.breakpoints == ["max-width: 768px"]
    assert digest.layout_signals.get("grid") == 1


def test_breakpoints_are_ordered_by_width() -> None:
    css = "@media (max-width: 620px){a{color:red}}@media (min-width: 1240px){a{color:red}}@media (max-width: 1024px){a{color:red}}"
    assert build_style_digest(css).breakpoints == ["max-width: 620px", "max-width: 1024px", "min-width: 1240px"]


def test_palette_does_not_repeat_a_named_colour_as_a_literal() -> None:
    """`--paper: #FFFFFF` and the `#fff` that references it are one colour."""
    css = ":root{--paper:#FFFFFF}body{background:#fff}.card{background:#fff}"
    palette = build_style_digest(css).palette
    assert palette == ["--paper: #FFFFFF"]


def test_a_redeclared_custom_property_is_one_token() -> None:
    css = ':root{--font-display:"A2Z",sans-serif}@media (max-width:620px){:root{--font-display:"Pretendard",sans-serif}}'
    assert build_style_digest(css).fonts == ["--font-display: A2Z"]


def test_script_digest_reports_behaviour_not_code(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    digest = build_script_digest(collect_js(bundle, bundle.entry, 512000))
    assert "Swiper" in digest.libraries
    assert "click handlers" in digest.interactions
    assert "addEventListener" not in digest.model_dump_json()


def test_asset_inventory_reads_real_dimensions(tmp_path: Path) -> None:
    bundle = discover(_site(tmp_path))
    infos = {info.path: info for info in build_asset_infos(bundle)}
    hero = infos["assets/hero.png"]
    assert (hero.width, hero.height) == (1600, 900)
    assert hero.aspect == 1.78
    assert "hero" in hero.usable_as
    assert "icon" in infos["assets/icon.png"].usable_as


def test_image_dimensions_tolerates_unreadable_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert image_dimensions(broken) == (None, None)


def test_source_assets_digest_stays_small(tmp_path: Path) -> None:
    """The digest rides in every later prompt, so its size is a contract."""
    bundle = discover(_site(tmp_path))
    styles = build_style_digest(collect_css(bundle, bundle.entry, 512000))
    scripts = build_script_digest(collect_js(bundle, bundle.entry, 512000))
    assert len(styles.model_dump_json()) + len(scripts.model_dump_json()) < 4096


def test_discover_ignores_dotted_and_underscored_folders(tmp_path: Path) -> None:
    root = _site(tmp_path)
    for folder in (".backups", "_drafts"):
        hidden = root / folder
        hidden.mkdir()
        (hidden / "index.html.20260909_010203_000000.bak").write_text("<html>old</html>", encoding="utf-8")
        (hidden / "draft.html").write_text("<html>draft</html>", encoding="utf-8")
    bundle = discover(root)
    assert [page.relative for page in bundle.pages] == ["index.html", "about.html"]
    assert not [item for item in bundle.files if item.relative.startswith((".", "_"))]


def _pages(root: Path, pages: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in pages.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"<html><head><title>{name}</title></head><body>{body}</body></html>", encoding="utf-8")
    return root


def test_pages_whose_names_collapse_to_one_slug_get_distinct_slugs(tmp_path: Path) -> None:
    root = _pages(tmp_path / "input", {"index.html": "", "a_b.html": "", "a-b.html": "", "a-b-2.html": ""})
    first = {page.relative: page.slug for page in discover(root).pages}
    second = {page.relative: page.slug for page in discover(root).pages}
    assert first == second
    assert len(set(first.values())) == 4
    assert first["a-b.html"] == "a-b"
    assert first["a-b-2.html"] == "a-b-2"
    assert first["index.html"] == "index"


def test_rewrite_keeps_query_and_fragment(tmp_path: Path) -> None:
    root = _site(tmp_path)
    (root / "icons.svg").write_text("<svg/>", encoding="utf-8")
    bundle = discover(root)
    html = rewrite_asset_paths(
        '<a href="about.html?lang=en#team">T</a><img src="icons.svg#logo">'
        '<img srcset="/src/img/hero.png?v=2 2x, icons.svg#logo 1x"><div style="background:url(\'/src/img/hero.png#x\')"></div>',
        bundle.render_map,
        page_map_for(bundle),
    )
    assert 'href="about.html?lang=en#team"' in html
    assert 'src="assets/icons.svg#logo"' in html
    assert 'srcset="assets/hero.png?v=2 2x, assets/icons.svg#logo 1x"' in html
    assert "url('assets/hero.png#x')" in html


def _spaced_site(tmp_path: Path) -> Path:
    root = _site(tmp_path)
    (root / "img").mkdir()
    _png(root / "img" / "hero image.png", 10, 10)
    return root


def test_rewrite_maps_a_percent_encoded_reference(tmp_path: Path) -> None:
    bundle = discover(_spaced_site(tmp_path))
    html = rewrite_asset_paths(
        '<img src="img/hero%20image.png?v=1#x"><img srcset="img/hero%20image.png 2x"><div style="background:url(img/hero%20image.png)"></div>',
        bundle.render_map,
    )
    assert 'src="assets/hero%20image.png?v=1#x"' in html
    assert 'srcset="assets/hero%20image.png 2x"' in html
    assert "url(assets/hero%20image.png)" in html


def test_rewrite_still_maps_an_unencoded_reference_with_spaces(tmp_path: Path) -> None:
    bundle = discover(_spaced_site(tmp_path))
    html = rewrite_asset_paths('<img src="img/hero image.png">', bundle.render_map)
    assert 'src="assets/hero%20image.png"' in html


@pytest.mark.parametrize(
    "reference",
    ["%2e%2e/%2e%2e/etc/passwd", "..%2f..%2fsecret.png", "img%5C..%5C..%5Csecret.png", "img/hero%00.png", "%00", "%2Fetc%2Fpasswd"],
)
def test_rewrite_does_not_let_encoding_reach_outside_the_asset_map(tmp_path: Path, reference: str) -> None:
    bundle = discover(_spaced_site(tmp_path))
    source = f'<img src="{reference}">'
    assert rewrite_asset_paths(source, bundle.render_map) == source


def test_encoded_traversal_resolves_like_its_plain_equivalent(tmp_path: Path) -> None:
    bundle = discover(_spaced_site(tmp_path))
    encoded = rewrite_asset_paths('<img src="%2e%2e/img/hero%20image.png">', bundle.render_map, source="a/b.html")
    plain = rewrite_asset_paths('<img src="../img/hero image.png">', bundle.render_map, source="a/b.html")
    assert encoded == plain


def test_same_basename_pages_in_different_folders_keep_their_own_links(tmp_path: Path) -> None:
    root = _pages(tmp_path / "input", {
        "index.html": '<a href="en/index.html">en</a><a href="ko/index.html">ko</a><a href="about.html">a</a>',
        "en/index.html": '<a href="index.html">self</a><a href="../about.html">up</a>',
        "ko/index.html": "",
        "docs/about.html": "",
    })
    bundle = discover(root)
    pages = {page.relative: page for page in bundle.pages}
    page_map = page_map_for(bundle)
    root_html = rewrite_asset_paths(pages["index.html"].html, bundle.render_map, page_map, "index.html")
    assert f'href="{pages["en/index.html"].slug}.html"' in root_html
    assert f'href="{pages["ko/index.html"].slug}.html"' in root_html
    assert f'href="{pages["docs/about.html"].slug}.html"' in root_html
    en_html = rewrite_asset_paths(pages["en/index.html"].html, bundle.render_map, page_map, "en/index.html")
    assert f'href="{pages["en/index.html"].slug}.html">self' in en_html


def test_copied_stylesheet_urls_follow_the_new_placement(tmp_path: Path) -> None:
    root = _pages(tmp_path / "input", {"index.html": ""})
    (root / "css").mkdir()
    (root / "img").mkdir()
    (root / "css" / "base.css").write_text("a{color:red}", encoding="utf-8")
    (root / "css" / "site.css").write_text(
        '@import "base.css";body{background:url("../img/hero.png?v=1#x")}',
        encoding="utf-8",
    )
    _png(root / "img" / "hero.png", 10, 10)
    bundle = discover(root)
    run_dir = tmp_path / "run"
    copy_assets(bundle, run_dir)
    css = (run_dir / "assets" / "site.css").read_text(encoding="utf-8")
    assert 'url("hero.png?v=1#x")' in css
    assert '@import "base.css"' in css
    assert (run_dir / "assets" / "hero.png").is_file()
    assert (run_dir / "assets" / "base.css").is_file()
