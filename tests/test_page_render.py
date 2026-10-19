import importlib.util
import struct
import zlib

import pytest

from wra.browser.guard import launch_guarded
from wra.generator.overflow_repair import _measure, repair_horizontal_overflow
from wra.generator.page_render import new_guarded_page, page_loader


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

_HTML = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0"><img src="assets/wide.png"></body></html>'


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _scroll_widths(asset_root) -> dict[int, int]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, page_loader(asset_root) as load:
        browser = playwright.chromium.launch()
        try:
            snapshots = _measure(browser.new_page(), _HTML, [], load)
        finally:
            browser.close()
    return {width: snapshot["scrollWidth"] for width, snapshot in snapshots.items()}


def test_assets_load_only_when_an_asset_root_is_given(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "wide.png").write_bytes(_png(1200, 40))

    assert _scroll_widths(None)[390] == 390
    assert _scroll_widths(tmp_path)[390] == 1200
    assert list(tmp_path.glob(".wra-repair-*")) == []


def test_preview_file_is_removed_when_rendering_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with page_loader(tmp_path) as load:
            class Page:
                def goto(self, url, **kwargs):
                    raise RuntimeError("boom")

            load(Page(), _HTML)

    assert list(tmp_path.glob(".wra-repair-*")) == []


def test_repair_leaves_html_and_directory_untouched(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "wide.png").write_bytes(_png(1200, 40))

    assert repair_horizontal_overflow(_HTML, asset_root=tmp_path) == (_HTML, [])
    assert sorted(path.name for path in tmp_path.iterdir()) == ["assets"]


def test_guarded_page_still_loads_assets_under_the_asset_root(tmp_path):
    from playwright.sync_api import sync_playwright

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "wide.png").write_bytes(_png(1200, 40))

    with sync_playwright() as playwright, page_loader(tmp_path) as load:
        browser = playwright.chromium.launch()
        try:
            page = new_guarded_page(browser, tmp_path)
            load(page, _HTML)
            width = page.evaluate("document.querySelector('img').naturalWidth")
        finally:
            browser.close()

    assert width == 1200


def test_guarded_page_blocks_files_outside_the_asset_root(tmp_path):
    from playwright.sync_api import sync_playwright

    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.png").write_bytes(_png(1200, 40))
    html = _HTML.replace("assets/wide.png", "../outside.png")

    with sync_playwright() as playwright, page_loader(root) as load:
        browser = playwright.chromium.launch()
        try:
            page = new_guarded_page(browser, root)
            load(page, html)
            width = page.evaluate("document.querySelector('img').naturalWidth")
        finally:
            browser.close()

    assert width == 0


def test_guarded_page_without_asset_root_blocks_file_subresources(tmp_path):
    from playwright.sync_api import sync_playwright

    image = tmp_path / "secret.png"
    image.write_bytes(_png(1200, 40))
    html = _HTML.replace("assets/wide.png", image.resolve().as_uri())

    with sync_playwright() as playwright, page_loader(None) as load:
        browser = launch_guarded(playwright)
        try:
            page = new_guarded_page(browser, None)
            load(page, html)
            page.wait_for_timeout(300)
            width = page.evaluate("document.querySelector('img').naturalWidth")
        finally:
            browser.close()

    assert width == 0


def test_guarded_page_websocket_does_not_reach_a_local_listener():
    import socket

    from playwright.sync_api import sync_playwright

    html = "<!DOCTYPE html><html><body><script>new WebSocket('ws://127.0.0.1:%d/')</script></body></html>"

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.5)
        with sync_playwright() as playwright, page_loader(None) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, None)
                load(page, html % listener.getsockname()[1])
                page.wait_for_timeout(500)
            finally:
                browser.close()
        with pytest.raises(socket.timeout):
            listener.accept()
