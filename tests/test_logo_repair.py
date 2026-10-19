import importlib.util
import struct
import zlib

import pytest

from wra.browser.evaluator import HEADER_LOGO_SCRIPT
from wra.generator.logo_repair import _measure, repair_oversized_header_logo
from wra.generator.page_render import page_loader


def _playwright_launch_fails() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return True
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return False
    except Exception:
        return True


pytestmark = pytest.mark.skipif(_playwright_launch_fails(), reason="playwright unavailable")

_PAGE = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0">%s</body></html>'
_NAV_LOGO = _PAGE % '<nav class="navbar"><a href="#"><img src="assets/logo.png" width="1024" height="1024"></a></nav><main>text</main>'
_BANNER = _PAGE % '<header class="hero"><img src="assets/logo.png" style="width:100%;height:200px"></header><main>text</main>'
_SMALL_LOGO = _PAGE % '<nav class="navbar"><a href="#"><img src="assets/logo.png" style="height:40px"></a></nav><main>text</main>'


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@pytest.fixture
def asset_root(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(_png(64, 64))
    return tmp_path


def _scroll_widths(html: str, asset_root) -> dict[int, int]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, page_loader(asset_root) as load:
        browser = playwright.chromium.launch()
        try:
            snapshots = _measure(browser.new_page(), html, load)
        finally:
            browser.close()
    return {width: snapshot["scrollWidth"] for width, snapshot in snapshots.items()}


def _header_logo_hits(html: str, asset_root) -> list[dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, page_loader(asset_root) as load:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 800})
            load(page, html)
            return page.evaluate(HEADER_LOGO_SCRIPT)
        finally:
            browser.close()


def test_a_logo_in_a_nav_home_link_is_capped_at_40px(asset_root):
    assert _scroll_widths(_NAV_LOGO, asset_root)[390] == 1024

    repaired, repairs = repair_oversized_header_logo(_NAV_LOGO, asset_root=asset_root)

    assert len(repairs) == 1
    assert repairs[0].endswith("1024x1024->40px")
    assert _scroll_widths(repaired, asset_root) == {390: 390, 768: 768, 1280: 1280}
    assert list(asset_root.glob(".wra-repair-*")) == []


def test_a_wide_banner_image_outside_logo_context_is_left_alone(asset_root):
    assert repair_oversized_header_logo(_BANNER, asset_root=asset_root) == (_BANNER, [])


def test_a_narrow_tall_hero_image_in_a_plain_header_is_left_alone(asset_root):
    html = _PAGE % '<header class="hero"><div style="display:flex"><div style="width:50%">text</div><img src="assets/logo.png" style="width:45%;height:300px"></div></header><main>text</main>'

    assert repair_oversized_header_logo(html, asset_root=asset_root) == (html, [])


def test_a_contained_hero_illustration_in_a_header_is_not_reported(asset_root):
    html = _PAGE % '<header class="hero" style="padding:20px"><h1>Title</h1><img src="assets/logo.png" style="width:45%;height:400px"></header><main>text</main>'

    assert _header_logo_hits(html, asset_root) == []
    assert repair_oversized_header_logo(html, asset_root=asset_root) == (html, [])


def test_a_hero_illustration_overflowing_the_header_is_reported_but_not_repaired(asset_root):
    html = _PAGE % '<header class="hero" style="height:120px"><h1>Title</h1><img src="assets/logo.png" style="width:45%;height:400px"></header><main>text</main>'

    assert len(_header_logo_hits(html, asset_root)) == 1
    assert repair_oversized_header_logo(html, asset_root=asset_root) == (html, [])


def test_a_home_link_in_an_article_header_is_not_logo_context(asset_root):
    html = _PAGE % '<article><header><a href="/"><img src="assets/logo.png" style="width:80px;height:300px"></a></header></article>'

    assert repair_oversized_header_logo(html, asset_root=asset_root) == (html, [])


def test_header_logo_script_ignores_a_footer_nav_image(asset_root):
    html = _PAGE % '<footer><nav><a href="/"><img src="assets/logo.png" style="width:20%;height:300px"></a></nav></footer>'

    assert _header_logo_hits(html, asset_root) == []


def test_a_normal_logo_is_left_alone(asset_root):
    assert repair_oversized_header_logo(_SMALL_LOGO, asset_root=asset_root) == (_SMALL_LOGO, [])


def test_header_logo_script_reports_the_nav_logo_but_not_the_banner(asset_root):
    hits = _header_logo_hits(_NAV_LOGO, asset_root)
    assert len(hits) == 1
    assert (hits[0]["width"], hits[0]["height"]) == (1024, 1024)
    assert _header_logo_hits(_BANNER, asset_root) == []
