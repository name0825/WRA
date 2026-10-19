import io
import json

from PIL import Image

from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
from wra.core.models import FeatureModel
from wra.designer.schema import (
    ColorSystem,
    ComponentSpec,
    Design,
    DesignSpec,
    FrameworkRecommendation,
    FrameworkRecommendations,
    InteractionSpec,
    Motion,
    ResponsiveSpec,
    SectionSpec,
    Typography,
)
from wra.evaluator.llm import _EVALUATOR_CHARS_PER_TOKEN, _MAX_FAILURE_CHARS, LLMEvaluator
from wra.evaluator.screenshots import MIN_SCREENSHOT_PIXELS, PIXELS_PER_IMAGE_TOKEN, fit_screenshots
from wra.llm.client import LLMClient
from wra.llm.context import OUTPUT_HEADROOM_TOKENS, estimate_num_ctx


def _png(path, size):
    Image.new("RGB", size, (200, 120, 40)).save(path)
    return str(path)


def test_no_cap_returns_the_same_paths_without_opening_files():
    paths = ["/missing/desktop.png", "/missing/mobile.png"]

    assert fit_screenshots(paths, None) == paths


def test_sum_within_cap_returns_the_same_paths(tmp_path):
    paths = [_png(tmp_path / "a.png", (100, 200)), _png(tmp_path / "b.png", (50, 80))]

    assert fit_screenshots(paths, 24000) == paths


def test_sum_over_cap_downsizes_to_bytes_close_to_the_cap_and_keeps_the_files(tmp_path):
    sizes = [(1440, 6000), (768, 5000), (390, 4000)]
    paths = [_png(tmp_path / f"{index}.png", size) for index, size in enumerate(sizes)]
    before = [open(path, "rb").read() for path in paths]
    cap = 2_000_000

    fitted = fit_screenshots(paths, cap)

    assert all(isinstance(item, bytes) for item in fitted)
    resized = [Image.open(io.BytesIO(item)).size for item in fitted]
    total = sum(width * height for width, height in resized)
    assert 0.97 * cap <= total <= cap
    for (width, height), (new_width, new_height) in zip(sizes, resized):
        assert abs(new_width - new_height * width / height) <= 1
    assert [open(path, "rb").read() for path in paths] == before


DIMENSIONS = (
    "design_completeness",
    "visual_hierarchy",
    "design_consistency",
    "content_preservation",
    "ux",
    "readability",
    "responsive_design",
    "originality",
    "html_quality",
)


def _evaluation_response() -> str:
    payload = {name: {"score": 8, "feedback": f"{name} feedback"} for name in DIMENSIONS}
    payload["overall_score"] = 8
    payload["summary"] = "Solid."
    payload["improvement_suggestions"] = []
    return json.dumps(payload)


def make_spec() -> DesignSpec:
    return DesignSpec(
        design=Design(
            concept="Test",
            visual_direction="Minimal",
            layout="Single column",
            color_system=ColorSystem(background="#fff", surface="#f5f5f5", text="#111", muted_text="#666", primary="#000", accent="#333"),
            typography=Typography(heading="sans-serif", body="sans-serif", scale="standard", weight="400"),
            framework=FrameworkRecommendations(css=FrameworkRecommendation(name="none", reason="test"), js=FrameworkRecommendation(name="none", reason="test")),
            motion=Motion(level="none"),
        ),
        sections=[SectionSpec(id="main", purpose="Main content", layout="single", components=[ComponentSpec(type="text", purpose="Content", content_source="content.text")])],
        responsive=ResponsiveSpec(strategy="responsive"),
        interaction=InteractionSpec(),
    )


class RecordingLLMClient(LLMClient):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "options": options or {}, **kwargs})
        return _evaluation_response()


def _evaluate_with_screenshots(tmp_path, sizes: list[tuple[int, int]], html: str, min_num_ctx: int = 32768) -> RecordingLLMClient:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    viewports = []
    for index, size in enumerate(sizes):
        screenshot = tmp_path / f"{index}.png"
        Image.new("RGB", size, (255, 255, 255)).save(screenshot)
        viewports.append(BrowserViewportResult(name=f"viewport{index}", width=size[0], height=size[1], screenshot_path=str(screenshot)))
    client = RecordingLLMClient()
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=min_num_ctx, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    evaluator.evaluate(FeatureModel(), make_spec(), html, browser_result=BrowserEvaluationResult(enabled=True, viewports=viewports))
    return client


def test_reference_polished_sized_evaluation_fits_num_ctx(tmp_path):
    client = _evaluate_with_screenshots(tmp_path, [(1440, 7115), (768, 6038), (390, 7036)], "<html>" + "x" * 52000 + "</html>")

    prompt = client.calls[0]["prompt"]
    images = client.calls[0]["images"]
    num_ctx = client.calls[0]["options"]["num_ctx"]
    assert len(prompt) >= 52000
    assert all(isinstance(item, bytes) for item in images)
    fitted_pixels = sum(Image.open(io.BytesIO(item)).size[0] * Image.open(io.BytesIO(item)).size[1] for item in images)
    assert fitted_pixels >= MIN_SCREENSHOT_PIXELS
    assert num_ctx == 32768
    assert int(len(prompt) / _EVALUATOR_CHARS_PER_TOKEN) + fitted_pixels // PIXELS_PER_IMAGE_TOKEN + OUTPUT_HEADROOM_TOKENS <= num_ctx


def test_small_screenshots_pass_through_unscaled(tmp_path):
    client = _evaluate_with_screenshots(tmp_path, [(1440, 900), (1440, 900), (1440, 900)], "<html></html>")

    assert client.calls[0]["images"] == [str(tmp_path / f"{index}.png") for index in range(3)]


def test_screenshot_scale_tracks_real_token_ratio(tmp_path):
    client = _evaluate_with_screenshots(tmp_path, [(1440, 7115), (768, 6038), (390, 7036)], "<html>" + "x" * 69000 + "</html>")

    assert len(client.calls[0]["prompt"]) >= 69000
    images = client.calls[0]["images"]
    fitted_pixels = sum(Image.open(io.BytesIO(item)).size[0] * Image.open(io.BytesIO(item)).size[1] for item in images)
    original_pixels = 1440 * 7115 + 768 * 6038 + 390 * 7036
    assert fitted_pixels >= 0.55 ** 2 * original_pixels


def test_image_tokens_raise_num_ctx(tmp_path):
    client = _evaluate_with_screenshots(tmp_path, [(1440, 4000), (1440, 4000), (1440, 4000)], "<html></html>", min_num_ctx=8192)

    prompt = client.calls[0]["prompt"]
    assert client.calls[0]["options"]["num_ctx"] > estimate_num_ctx(prompt, 8192, 32768, ceiling=32768)


def test_missing_screenshot_is_dropped_not_fatal(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    screenshot = _png(tmp_path / "0.png", (1440, 900))
    viewports = [
        BrowserViewportResult(name="desktop", width=1440, height=900, screenshot_path=screenshot),
        BrowserViewportResult(name="mobile", width=390, height=844, screenshot_path=str(tmp_path / "missing.png")),
    ]
    client = RecordingLLMClient()
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=32768, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>", browser_result=BrowserEvaluationResult(enabled=True, viewports=viewports))

    assert result.overall_score == 8
    assert client.calls[0]["images"] == [screenshot]


class FailOnceLLMClient(RecordingLLMClient):
    def generate(self, prompt: str, model: str, options=None, **kwargs) -> str:
        if not self.calls:
            self.calls.append({"prompt": prompt, "options": options or {}, **kwargs})
            raise ValueError("x" * 10000)
        return super().generate(prompt, model, options, **kwargs)


def test_repair_prompt_truncates_long_failure(tmp_path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("Evaluate this page.", encoding="utf-8")
    client = FailOnceLLMClient()
    evaluator = LLMEvaluator(client, "evaluator-model", prompt_path=prompt_path, min_num_ctx=32768, max_num_ctx=32768, ceiling_num_ctx=32768, max_retries=1, growth_power=2.0)

    result = evaluator.evaluate(FeatureModel(), make_spec(), "<html></html>")

    assert result.overall_score == 8
    retry_prompt = client.calls[1]["prompt"]
    assert "x" * _MAX_FAILURE_CHARS in retry_prompt
    assert "x" * (_MAX_FAILURE_CHARS + 1) not in retry_prompt
