import json
from pathlib import Path

from wra.analyzer.vision import VisionAnalyzer
from wra.core.models import AssetInfo, VisionConfig


class RecordingClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, prompt, model, options=None, images=None, *, think=None, format=None, timeout=None):
        self.calls.append({"prompt": prompt, "model": model, "images": images, "format": format, "options": options})
        return self.response


INSIGHT = json.dumps({
    "subject": "CNC end mill close-up",
    "mood": ["industrial", "metallic", "dark"],
    "role": "product",
    "has_text": False,
    "text": None,
})

IMPRESSION = json.dumps({
    "mood": ["corporate", "dense", "flat"],
    "density": "tight, little whitespace",
    "layout": "Centered single column with stacked cards.",
    "strengths": ["clear product photography"],
    "weaknesses": ["weak typographic hierarchy", "low contrast"],
})


def _analyzer(tmp_path: Path, client, **overrides) -> VisionAnalyzer:
    settings = {"cache_directory": str(tmp_path / "cache"), **overrides}
    return VisionAnalyzer(llm_client=client, model="test-vlm", config=VisionConfig(**settings))


def _image(tmp_path: Path, name: str, size: int = 8192) -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * size)
    return path


def test_selects_only_images_worth_a_call(tmp_path: Path) -> None:
    analyzer = _analyzer(tmp_path, RecordingClient(INSIGHT), max_images=2)
    assets = [
        AssetInfo(path="assets/big.png", kind="image", bytes=90000, width=1600, height=900),
        AssetInfo(path="assets/mid.png", kind="image", bytes=50000, width=800, height=600),
        AssetInfo(path="assets/tiny.png", kind="image", bytes=100, width=16, height=16),
        AssetInfo(path="assets/icon.png", kind="image", bytes=9000, width=32, height=32),
        AssetInfo(path="assets/site.css", kind="css", bytes=90000),
    ]
    selected = [asset.path for asset in analyzer.select_images(assets)]
    assert selected == ["assets/big.png", "assets/mid.png"]


def test_skips_formats_the_model_cannot_decode(tmp_path: Path) -> None:
    """ICO and SVG only ever buy a 400 from Ollama."""
    analyzer = _analyzer(tmp_path, RecordingClient(INSIGHT))
    assets = [
        AssetInfo(path="assets/Logo_64.ico", kind="image", bytes=90000, width=64, height=64),
        AssetInfo(path="assets/diagram.svg", kind="image", bytes=90000, width=800, height=600),
        AssetInfo(path="assets/photo.png", kind="image", bytes=90000, width=800, height=600),
    ]
    assert [asset.path for asset in analyzer.select_images(assets)] == ["assets/photo.png"]


def test_concurrent_analysis_covers_every_image(tmp_path: Path) -> None:
    client = RecordingClient(INSIGHT)
    analyzer = _analyzer(tmp_path, client, concurrency=4, cache_directory="")
    assets = [AssetInfo(path=f"assets/photo{index}.png", kind="image", bytes=90000, width=800, height=600) for index in range(6)]
    paths = {asset.path: _image(tmp_path, Path(asset.path).name, size=8192 + index) for index, asset in enumerate(assets)}
    analyzer.analyze_assets(assets, paths)

    assert all(asset.vision is not None for asset in assets)
    assert len(client.calls) == 6


def test_concurrent_analysis_isolates_one_failure(tmp_path: Path) -> None:
    class OneBadImage(RecordingClient):
        def generate(self, prompt, model, options=None, images=None, *, think=None, format=None, timeout=None):
            if images and "photo2.png" in images[0]:
                raise RuntimeError("Failed to load image or audio file")
            return self.response

    analyzer = _analyzer(tmp_path, OneBadImage(INSIGHT), concurrency=4, cache_directory="")
    assets = [AssetInfo(path=f"assets/photo{index}.png", kind="image", bytes=90000, width=800, height=600) for index in range(4)]
    paths = {asset.path: _image(tmp_path, Path(asset.path).name, size=8192 + index) for index, asset in enumerate(assets)}
    analyzer.analyze_assets(assets, paths)

    analysed = [asset.path for asset in assets if asset.vision is not None]
    assert analysed == ["assets/photo0.png", "assets/photo1.png", "assets/photo3.png"]


def test_attaches_insight_to_the_asset(tmp_path: Path) -> None:
    client = RecordingClient(INSIGHT)
    analyzer = _analyzer(tmp_path, client)
    asset = AssetInfo(path="assets/hero.png", kind="image", bytes=90000, width=1600, height=900)
    analyzer.analyze_assets([asset], {"assets/hero.png": _image(tmp_path, "hero.png")})

    assert asset.vision is not None
    assert asset.vision.role == "product"
    assert asset.vision.mood == ["industrial", "metallic", "dark"]
    assert len(client.calls) == 1
    # A schema, not the bare "json" grammar, and the insight one at that.
    assert isinstance(client.calls[0]["format"], dict)
    assert client.calls[0]["format"]["title"] == "VisionInsight"


def test_a_failing_image_does_not_break_the_run(tmp_path: Path) -> None:
    class Failing(RecordingClient):
        def generate(self, *args, **kwargs):
            raise RuntimeError("model unavailable")

    analyzer = _analyzer(tmp_path, Failing(""))
    asset = AssetInfo(path="assets/hero.png", kind="image", bytes=90000, width=1600, height=900)
    analyzer.analyze_assets([asset], {"assets/hero.png": _image(tmp_path, "hero.png")})
    assert asset.vision is None
    assert asset.width == 1600


def test_cache_prevents_a_second_call_for_the_same_file(tmp_path: Path) -> None:
    client = RecordingClient(INSIGHT)
    path = _image(tmp_path, "hero.png")
    for _ in range(2):
        analyzer = _analyzer(tmp_path, client)
        asset = AssetInfo(path="assets/hero.png", kind="image", bytes=90000, width=1600, height=900)
        analyzer.analyze_assets([asset], {"assets/hero.png": path})
        assert asset.vision is not None
    assert len(client.calls) == 1


def test_page_impression_is_capped(tmp_path: Path) -> None:
    client = RecordingClient(IMPRESSION)
    analyzer = _analyzer(tmp_path, client)
    impression = analyzer.analyze_page(_image(tmp_path, "source.png"))
    # analyze_page and _analyze_one share _request but not their schema.
    assert client.calls[0]["format"]["title"] == "PageImpression"
    assert impression is not None
    assert impression.weaknesses == ["weak typographic hierarchy", "low contrast"]
    assert len(impression.mood) <= 5


def test_vision_request_carries_the_pinned_num_ctx(tmp_path: Path) -> None:
    client = RecordingClient(IMPRESSION)
    analyzer = VisionAnalyzer(llm_client=client, model="test-vlm", config=VisionConfig(cache_directory=str(tmp_path / "cache")), num_ctx=32768)
    analyzer.analyze_page(_image(tmp_path, "source.png"))
    assert client.calls[0]["options"] == {"num_ctx": 32768}


def test_vision_request_without_a_pinned_num_ctx_sends_no_options(tmp_path: Path) -> None:
    client = RecordingClient(IMPRESSION)
    _analyzer(tmp_path, client).analyze_page(_image(tmp_path, "source.png"))
    assert client.calls[0]["options"] is None


def test_malformed_response_yields_no_impression(tmp_path: Path) -> None:
    analyzer = _analyzer(tmp_path, RecordingClient("not json at all"))
    assert analyzer.analyze_page(_image(tmp_path, "source.png")) is None


def test_source_screenshot_goes_through_the_browser_guard(tmp_path: Path, monkeypatch) -> None:
    import sys
    import types
    from contextlib import contextmanager

    from wra.analyzer import vision
    from wra.browser.models import BrowserConfig

    seen: dict = {}

    class FakePage:
        def goto(self, url, **kwargs):
            seen["url"] = url

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def screenshot(self, path, **kwargs):
            Path(path).write_bytes(b"png")

    class FakeContext:
        def new_page(self):
            seen["guarded_before_page"] = "root" in seen
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, **kwargs):
            seen["viewport"] = kwargs.get("viewport")
            return FakeContext()

        def close(self):
            pass

    @contextmanager
    def fake_sync_playwright():
        yield object()

    fake_module = types.ModuleType("playwright.sync_api")
    fake_module.sync_playwright = fake_sync_playwright
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(vision, "launch_guarded", lambda playwright: seen.setdefault("launched", True) and FakeBrowser())
    monkeypatch.setattr(vision, "guard_context", lambda context, root: seen.__setitem__("root", root))

    result = vision.render_source_page("<p>x</p>", tmp_path, tmp_path / "out" / "source.png", BrowserConfig(), reveal_animations=False)

    assert result == tmp_path / "out" / "source.png"
    assert seen["launched"] is True
    assert seen["root"] == tmp_path.resolve()
    assert seen["guarded_before_page"] is True
    assert seen["viewport"] is not None
    assert seen["url"].endswith(vision.SOURCE_PREVIEW_NAME)
