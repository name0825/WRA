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
    assert bundle.asset_map == {"hero.png": "assets/hero.png", "icon.png": "assets/icon.png"}


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
