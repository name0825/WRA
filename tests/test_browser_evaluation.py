import struct
import zlib
from pathlib import Path

import pytest

from wra.browser.evaluator import PREVIEW_NAME, BrowserEvaluator
from wra.browser.models import BrowserConfig, BrowserViewport

pytest.importorskip("playwright.sync_api")

PAGE = '<html><body style="margin:0"><h1>Hi</h1><img src="assets/hero.png" width="400"></body></html>'


def _png(path: Path, width: int, height: int) -> None:
    raw = b"".join(b"\0" + bytes((20, 40, 90)) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    _png(tmp_path / "assets" / "hero.png", 200, 120)
    return tmp_path


def _evaluate(html: str, run_dir: Path, output_dir: Path, asset_root: Path | None):
    config = BrowserConfig(enabled=True, viewports=[BrowserViewport(name="desktop", width=800, height=600)])
    return BrowserEvaluator(config).evaluate(html=html, output_dir=output_dir, iteration=1, asset_root=asset_root)


def test_the_page_is_rendered_where_its_assets_resolve(run_dir: Path) -> None:
    """Screenshots of a page whose every image failed to load are not evidence."""
    result = _evaluate(PAGE, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    viewport = result.viewports[0]

    assert [error for error in viewport.console_errors if "ERR_FILE_NOT_FOUND" in error] == []
    assert Path(viewport.screenshot_path).is_file()


def test_rendering_from_the_temp_directory_alone_loses_the_assets(run_dir: Path) -> None:
    """The behaviour the asset_root argument exists to correct."""
    result = _evaluate(PAGE, run_dir, run_dir / ".browser_tmp", asset_root=None)
    viewport = result.viewports[0]
    assert any("ERR_FILE_NOT_FOUND" in error for error in viewport.console_errors)


def test_the_preview_file_does_not_survive_the_run(run_dir: Path) -> None:
    _evaluate(PAGE, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    assert not (run_dir / PREVIEW_NAME).exists()


def test_overflow_is_still_reported(run_dir: Path) -> None:
    wide = '<html><body style="margin:0"><div style="width:2000px">wide</div></body></html>'
    result = _evaluate(wide, run_dir, run_dir / ".browser_tmp", asset_root=run_dir)
    assert result.viewports[0].horizontal_overflow
