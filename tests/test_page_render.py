import importlib.util
import struct
import zlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from wra.browser import evaluator as evaluator_module
from wra.browser.evaluator import FRAMEWORK_CACHE_DIRNAME, HTTP_CACHE_DIRNAME
from wra.browser.framework_cache import cached_path_for_url
from wra.browser.guard import launch_guarded
from wra.core.models import Framework, FrameworkConfig
from wra.generator.overflow_repair import _measure, repair_horizontal_overflow
from wra.generator.page_render import configure_preview_localization, new_guarded_page, page_loader
from wra.generator.table_wrap_repair import repair_wide_tables


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


CSS_URL = "https://cdn.example.test/framework.css"
CSS_HTML = f'<html><head><link rel="stylesheet" href="{CSS_URL}"></head><body><p>hi</p></body></html>'


class FakePage:
    def __init__(self):
        self.urls = []
        self.contents = []
        self.seen = []

    def goto(self, url, **kwargs):
        self.urls.append(url)
        path = Path(url2pathname(urlparse(url).path))
        self.contents.append(path.read_text(encoding="utf-8-sig"))
        self.seen.append(sorted(entry.name for entry in path.parent.iterdir()))

    def set_content(self, html, **kwargs):
        self.contents.append(html)


@pytest.fixture(autouse=True)
def reset_localization():
    configure_preview_localization(None, False)
    yield
    configure_preview_localization(None, False)


def _frameworks(tmp_path: Path, css: str = ".table-responsive{overflow-x:auto}") -> tuple[FrameworkConfig, Path]:
    cache_dir = tmp_path / "cache"
    frameworks = FrameworkConfig(css=[Framework(name="Test", cdn=CSS_URL, tag="link")])
    cached = cached_path_for_url(CSS_URL, ".css", cache_dir)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(css, encoding="utf-8")
    return frameworks, cache_dir


def test_configured_preview_is_localized_and_cleaned_up(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    frameworks, cache_dir = _frameworks(tmp_path)
    configure_preview_localization(frameworks, False, cache_dir=cache_dir, http_cache_dir=tmp_path / "http")
    page = FakePage()
    html = CSS_HTML

    with page_loader(root) as load:
        load(page, html)
        assert any(entry.name.startswith(f"{FRAMEWORK_CACHE_DIRNAME}_") for entry in root.iterdir())

    assert CSS_URL not in page.contents[0]
    assert f"{FRAMEWORK_CACHE_DIRNAME}_" in page.contents[0]
    assert html == CSS_HTML and CSS_URL in html
    assert list(root.iterdir()) == []


def test_unconfigured_preview_keeps_the_cdn_url(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    page = FakePage()

    with page_loader(root) as load:
        load(page, CSS_HTML)

    assert page.contents == [CSS_HTML]
    assert list(root.iterdir()) == []


def test_without_asset_root_set_content_is_used_unlocalized(tmp_path: Path) -> None:
    frameworks, cache_dir = _frameworks(tmp_path)
    configure_preview_localization(frameworks, False, cache_dir=cache_dir)
    page = FakePage()

    with page_loader(None) as load:
        load(page, CSS_HTML)

    assert page.urls == [] and page.contents == [CSS_HTML]


def test_localization_failure_falls_back_to_the_original_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "run"
    root.mkdir()
    frameworks = FrameworkConfig(css=[Framework(name="Test", cdn=CSS_URL, tag="link")])

    def failing_download(url, extension, cache_dir):
        raise OSError("offline")

    monkeypatch.setattr(evaluator_module, "ensure_cached_path_for_url", failing_download)
    configure_preview_localization(frameworks, False, cache_dir=tmp_path / "empty")
    page = FakePage()

    with page_loader(root) as load:
        load(page, CSS_HTML)

    assert page.contents == [CSS_HTML]
    assert list(root.iterdir()) == []


def test_cleanup_happens_when_the_body_raises(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    frameworks, cache_dir = _frameworks(tmp_path)
    configure_preview_localization(frameworks, False, cache_dir=cache_dir)

    with pytest.raises(RuntimeError):
        with page_loader(root) as load:
            load(FakePage(), CSS_HTML)
            raise RuntimeError("boom")

    assert list(root.iterdir()) == []


@pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="playwright not installed")
def test_repair_wide_tables_sees_the_localized_framework_css(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    frameworks, cache_dir = _frameworks(tmp_path)
    html = f"""<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="{CSS_URL}">
<style>body {{ margin: 0; }} td, th {{ padding: 8px; border: 1px solid #888; }}</style></head>
<body><div class="table-responsive"><table>
<tr><th>Name</th><th>Email</th></tr>
<tr><td>Kim</td><td>very.long.address.for.someone@subdomain.example-company.com</td></tr>
<tr><td>Lee</td><td>another.long.address.for.someone@subdomain.example-company.com</td></tr>
</table></div></body></html>"""

    unlocalized, unlocalized_repairs = repair_wide_tables(html, asset_root=root)
    assert unlocalized_repairs == []

    configure_preview_localization(frameworks, False, cache_dir=cache_dir)
    repaired, repairs = repair_wide_tables(html, asset_root=root)

    assert len(repairs) == 1 and "@390" in repairs[0]
    assert CSS_URL in repaired
    assert FRAMEWORK_CACHE_DIRNAME not in repaired and HTTP_CACHE_DIRNAME not in repaired
    assert list(root.iterdir()) == []
