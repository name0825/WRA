from pathlib import Path

import pytest

from wra.analyzer.vision import render_source_page
from wra.browser.models import BrowserConfig

pytest.importorskip("playwright.sync_api")

STYLE = (
    "body{margin:0;font:16px/1.5 system-ui;background:#fff}"
    "section{min-height:90vh;padding:4rem}"
    ".reveal{opacity:0;transform:translateY(40px);transition:opacity .6s,transform .6s}"
    ".reveal.on{opacity:1;transform:none}"
)
SECTIONS = "".join(
    f'<section><h2 class="reveal">Section {index}</h2><p class="reveal">Copy {index}</p></section>'
    for index in range(1, 4)
)
OBSERVER = (
    "<script>const io=new IntersectionObserver(entries=>entries.forEach(entry=>{"
    "if(entry.isIntersecting)entry.target.classList.add('on')}),{threshold:0.1});"
    "document.querySelectorAll('.reveal').forEach(el=>io.observe(el));</script>"
)


def _page(script: str = "") -> str:
    return f"<!DOCTYPE html><html><head><meta charset=utf-8><style>{STYLE}</style></head><body>{SECTIONS}{script}</body></html>"


def _ink_pixels(path: Path) -> int:
    """Count the pixels in a capture that are not the white page background."""
    import base64

    from playwright.sync_api import sync_playwright

    # Passed as a data URI: a blank page may not decode a file:// image.
    source = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            return page.evaluate(
                """async (url) => {
                    const image = new Image();
                    image.src = url;
                    await image.decode();
                    const canvas = document.createElement('canvas');
                    canvas.width = image.naturalWidth;
                    canvas.height = image.naturalHeight;
                    const context = canvas.getContext('2d');
                    context.drawImage(image, 0, 0);
                    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
                    let ink = 0;
                    for (let i = 0; i < data.length; i += 4) {
                        if (data[i] < 240 || data[i + 1] < 240 || data[i + 2] < 240) ink++;
                    }
                    return ink;
                }""",
                source,
            )
        finally:
            browser.close()


@pytest.mark.parametrize("script", ["", OBSERVER], ids=["reveal-script-never-runs", "intersection-observer"])
def test_reveal_pass_captures_content_that_animations_would_hide(tmp_path: Path, script: str) -> None:
    """A scroll-reveal page must not screenshot as blank space."""
    without = tmp_path / "without.png"
    with_reveal = tmp_path / "with.png"
    config = BrowserConfig()

    assert render_source_page(_page(script), tmp_path, without, config, reveal_animations=False) is not None
    assert render_source_page(_page(script), tmp_path, with_reveal, config, reveal_animations=True) is not None

    # Revealed text is ink on the page; content left transparent is not.
    assert _ink_pixels(with_reveal) > _ink_pixels(without) * 1.5


def test_a_page_whose_reveal_script_never_runs_would_capture_blank(tmp_path: Path) -> None:
    """The case the reveal pass exists for: no reveal logic, everything at opacity 0."""
    blank = tmp_path / "blank.png"
    revealed = tmp_path / "revealed.png"
    render_source_page(_page(), tmp_path, blank, BrowserConfig(), reveal_animations=False)
    render_source_page(_page(), tmp_path, revealed, BrowserConfig(), reveal_animations=True)

    assert _ink_pixels(blank) == 0
    assert _ink_pixels(revealed) > 0


def test_reveal_pass_leaves_no_preview_file_behind(tmp_path: Path) -> None:
    render_source_page(_page(OBSERVER), tmp_path, tmp_path / "source.png", BrowserConfig())
    assert list(tmp_path.glob(".source_preview.html")) == []
