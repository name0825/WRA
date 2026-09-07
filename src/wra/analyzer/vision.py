from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
from pathlib import Path

from wra.browser.models import BrowserConfig
from wra.core.models import AssetInfo, PageImpression, VisionConfig, VisionInsight
from wra.llm.client import LLMClient
from wra.llm.response import normalize_json_response
from wra.llm.schema import bound_strings

logger = logging.getLogger(__name__)

def _required(model, fields: list[str]) -> dict:
    """The model's schema with `fields` marked required.

    Every field on these two models carries a default, so pydantic emits no
    `required` list and an empty object satisfies the schema -- which is the
    collapse the schema was added to prevent. Both vision prompts ask for every
    key, so the contract is stated here rather than by giving the models
    defaults they do not want: a partial insight must still parse when it comes
    back from the cache written before this.
    """
    schema = bound_strings(model.model_json_schema())
    schema["required"] = fields
    return schema


VISION_INSIGHT_SCHEMA = _required(VisionInsight, ["subject", "mood", "role", "has_text", "text"])
PAGE_IMPRESSION_SCHEMA = _required(PageImpression, ["mood", "density", "layout", "strengths", "weaknesses"])

SOURCE_PREVIEW_NAME = ".source_preview.html"
# Ollama cannot decode these, so sending them only buys a 400 per file: ICO is
# an unsupported container, SVG is vector markup rather than an image.
UNSUPPORTED_SUFFIXES = {".ico", ".svg"}

# Scroll-reveal animations start their targets at `opacity: 0`, so a full-page
# screenshot of an untouched page captures mostly blank space. Walking the page
# the way a visitor does lets the site's own reveal logic run first.
_SCROLL_THROUGH = """
async (stepDelay) => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const pageHeight = () => document.documentElement.scrollHeight;
  for (let offset = 0; offset < pageHeight(); offset += window.innerHeight) {
    window.scrollTo(0, offset);
    await wait(stepDelay);
  }
  window.scrollTo(0, pageHeight());
  await wait(stepDelay * 2);
  window.scrollTo(0, 0);
  await wait(stepDelay);
}
"""

# Whatever the page's own logic left invisible is revealed directly. Only
# elements that are transparent yet still laid out are touched: anything hidden
# through `display` or `visibility` is hidden on purpose (menus, dialogs) and
# forcing it open would misrepresent the page rather than reveal it.
_REVEAL_REMAINING = """
() => {
  let revealed = 0;
  for (const element of document.body.querySelectorAll('*')) {
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (parseFloat(style.opacity) >= 0.05) continue;
    const box = element.getBoundingClientRect();
    if (box.width < 4 || box.height < 4) continue;
    element.style.setProperty('opacity', '1', 'important');
    element.style.setProperty('transform', 'none', 'important');
    element.style.setProperty('filter', 'none', 'important');
    revealed++;
  }
  const freeze = document.createElement('style');
  freeze.textContent = '*,*::before,*::after{transition:none!important;animation:none!important}';
  document.head.appendChild(freeze);
  return revealed;
}
"""


class VisionAnalyzer:
    """Read the source media with a vision model, once per input.

    The result travels inside the Feature Model and is therefore paid for in
    every later prompt, so the prompts cap their own output and this class caps
    how many images are ever sent.
    """

    def __init__(self, llm_client: LLMClient, model: str, config: VisionConfig, think: bool | None = None, timeout: float | None = None, image_prompt_path: str | Path = "prompts/analyzer/vision_image.md", page_prompt_path: str | Path = "prompts/analyzer/vision_page.md") -> None:
        self._llm_client = llm_client
        self._model = model
        self._config = config
        self._think = think
        self._timeout = timeout
        self._image_prompt_path = Path(image_prompt_path)
        self._page_prompt_path = Path(page_prompt_path)

    def select_images(self, assets: list[AssetInfo]) -> list[AssetInfo]:
        """Pick the images worth a vision call, largest first."""
        candidates = []
        for asset in assets:
            if asset.kind != "image" or asset.bytes < self._config.min_bytes:
                continue
            if Path(asset.path).suffix.lower() in UNSUPPORTED_SUFFIXES:
                continue
            if asset.width and asset.height and min(asset.width, asset.height) < self._config.min_dimension:
                continue
            candidates.append(asset)
        candidates.sort(key=lambda asset: asset.bytes, reverse=True)
        selected = candidates[: self._config.max_images]
        logger.info("Vision image selection: total=%d eligible=%d selected=%d", len(assets), len(candidates), len(selected))
        return selected

    def analyze_assets(self, assets: list[AssetInfo], source_paths: dict[str, Path]) -> None:
        """Attach a vision insight to each selected asset, in place."""
        prompt = self._image_prompt_path.read_text(encoding="utf-8")
        work = [(asset, source_paths[asset.path]) for asset in self.select_images(assets) if source_paths.get(asset.path) and source_paths[asset.path].is_file()]
        if not work:
            return
        workers = min(self._config.concurrency, len(work))
        logger.info("Vision analysis started: images=%d concurrency=%d", len(work), workers)
        if workers <= 1:
            for asset, path in work:
                self._analyze_one(prompt, asset, path)
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda item: self._analyze_one(prompt, *item), work))

    def _analyze_one(self, prompt: str, asset: AssetInfo, path: Path) -> None:
        try:
            data = self._request(prompt, path, VISION_INSIGHT_SCHEMA)
            asset.vision = VisionInsight.model_validate(self._coerce_insight(data))
            logger.info("Vision insight: asset=%s role=%s subject=%s", asset.path, asset.vision.role, asset.vision.subject)
        except Exception as exc:
            # One unreadable image must not cost the run its analysis; the
            # asset keeps the dimensions read straight off the file.
            logger.warning("Vision analysis skipped for %s: %s", asset.path, exc)

    def analyze_page(self, screenshot_path: str | Path) -> PageImpression | None:
        prompt = self._page_prompt_path.read_text(encoding="utf-8")
        try:
            data = self._request(prompt, Path(screenshot_path), PAGE_IMPRESSION_SCHEMA)
            impression = PageImpression.model_validate(self._coerce_impression(data))
            logger.info("Source page impression: mood=%s density=%s", impression.mood, impression.density)
            return impression
        except Exception as exc:
            logger.warning("Source page impression skipped: %s", exc)
            return None

    def _request(self, prompt: str, image_path: Path, schema: dict) -> dict:
        cached = self._read_cache(prompt, image_path)
        if cached is not None:
            logger.debug("Vision cache hit: %s", image_path)
            return cached
        response = self._llm_client.generate(prompt=prompt, model=self._model, options=None, images=[str(image_path)], think=self._think, format=schema, timeout=self._timeout)
        data = json.loads(normalize_json_response(response))
        if not isinstance(data, dict):
            raise ValueError("Vision model response must be a JSON object")
        self._write_cache(prompt, image_path, data)
        return data

    def _cache_path(self, prompt: str, image_path: Path) -> Path | None:
        directory = self._config.cache_directory
        if not directory:
            return None
        digest = hashlib.sha256()
        digest.update(image_path.read_bytes())
        digest.update(self._model.encode("utf-8"))
        digest.update(prompt.encode("utf-8"))
        return Path(directory) / f"{digest.hexdigest()}.json"

    def _read_cache(self, prompt: str, image_path: Path) -> dict | None:
        try:
            path = self._cache_path(prompt, image_path)
            if path is not None and path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.debug("Vision cache unreadable: %s", image_path)
        return None

    def _write_cache(self, prompt: str, image_path: Path, data: dict) -> None:
        try:
            path = self._cache_path(prompt, image_path)
            if path is None:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.debug("Vision cache not written: %s", image_path)

    @staticmethod
    def _as_list(value, limit: int) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:limit]

    @staticmethod
    def _coerce_insight(data: dict) -> dict:
        text = data.get("text")
        return {
            "subject": str(data.get("subject") or "").strip()[:80],
            "mood": VisionAnalyzer._as_list(data.get("mood"), 4),
            "role": str(data.get("role") or "").strip().lower()[:20],
            "has_text": bool(data.get("has_text")),
            "text": str(text).strip()[:60] if text else None,
        }

    @staticmethod
    def _coerce_impression(data: dict) -> dict:
        return {
            "mood": VisionAnalyzer._as_list(data.get("mood"), 5),
            "density": str(data.get("density") or "").strip()[:80],
            "layout": str(data.get("layout") or "").strip()[:120],
            "strengths": VisionAnalyzer._as_list(data.get("strengths"), 4),
            "weaknesses": VisionAnalyzer._as_list(data.get("weaknesses"), 4),
        }


def _reveal_animated_content(page, timeout_ms: int) -> None:
    """Bring scroll-revealed content into view before the page is captured."""
    try:
        page.evaluate(_SCROLL_THROUGH, min(200, max(60, timeout_ms // 100)))
        revealed = page.evaluate(_REVEAL_REMAINING)
        logger.info("Source page reveal pass: %d element(s) forced visible after scrolling", revealed)
    except Exception as exc:
        logger.warning("Source page reveal pass failed, capturing the page as it stands: %s", exc)


def render_source_page(html: str, asset_root: str | Path, screenshot_path: str | Path, browser_config: BrowserConfig, reveal_animations: bool = True) -> Path | None:
    """Screenshot the source page beside its copied assets so the media resolves.

    The page is rendered from `asset_root`, where the run's `assets/` directory
    lives, but the screenshot is written wherever the caller asks, so pages do
    not overwrite each other's captures.
    """
    preview_path = Path(asset_root) / SOURCE_PREVIEW_NAME
    screenshot_path = Path(screenshot_path)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Source page screenshot skipped: Playwright is not installed (pip install 'wra[browser]')")
        return None

    viewport = browser_config.viewports[0] if browser_config.viewports else None
    width, height = (viewport.width, viewport.height) if viewport else (1440, 900)
    preview_path.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.goto(preview_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=browser_config.timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=browser_config.timeout_ms)
                except Exception:
                    logger.debug("Source page networkidle timeout")
                if reveal_animations:
                    _reveal_animated_content(page, browser_config.timeout_ms)
                page.screenshot(path=str(screenshot_path), full_page=True)
                context.close()
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Source page screenshot failed: %s", exc)
        return None
    finally:
        preview_path.unlink(missing_ok=True)
    logger.info("Source page screenshot captured: %s (%dx%d)", screenshot_path, width, height)
    return screenshot_path
