from pathlib import Path
import logging

from wra.browser.models import BrowserConfig, BrowserEvaluationResult, BrowserViewportResult

logger = logging.getLogger(__name__)

PREVIEW_NAME = ".browser_preview.html"


class BrowserEvaluator:
    """Render generated HTML in Chromium and collect deterministic browser evidence."""

    def __init__(self, config: BrowserConfig) -> None:
        self._config = config

    def evaluate(self, html: str, output_dir: str | Path, iteration: int, asset_root: str | Path | None = None) -> BrowserEvaluationResult:
        if not self._config.enabled:
            logger.debug("Browser evaluation skipped: disabled")
            return BrowserEvaluationResult(enabled=False)
        if not self._config.viewports:
            logger.error("Browser evaluation has no configured viewports")
            raise ValueError("browser.viewports must contain at least one viewport when browser evaluation is enabled")
        logger.info("Browser evaluation started: iteration=%d viewports=%d", iteration, len(self._config.viewports))

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            logger.exception("Playwright is unavailable")
            raise RuntimeError("Playwright is required for browser evaluation. Install with: pip install 'wra[browser]'") from exc

        output_path = Path(output_dir)
        browser_dir = output_path / f"iteration_{iteration:03d}"
        browser_dir.mkdir(parents=True, exist_ok=True)
        # Rendered beside the run's `assets/` when there is one: a page loaded
        # from the temporary directory resolves `assets/...` against that
        # directory instead, and every image fails to load. Screenshots of a
        # page with no images are not evidence of how the page looks.
        page_root = Path(asset_root) if asset_root is not None else browser_dir
        page_root.mkdir(parents=True, exist_ok=True)
        html_path = page_root / (PREVIEW_NAME if asset_root is not None else "page.html")
        html_path.write_text(html, encoding="utf-8")
        results: list[BrowserViewportResult] = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for viewport in self._config.viewports:
                    logger.info("Browser viewport started: name=%s size=%dx%d", viewport.name, viewport.width, viewport.height)
                    console_errors: list[str] = []
                    page_errors: list[str] = []
                    context = browser.new_context(viewport={"width": viewport.width, "height": viewport.height})
                    page = context.new_page()
                    page.on("console", lambda message, errors=console_errors: errors.append(message.text) if message.type == "error" else None)
                    page.on("pageerror", lambda error, errors=page_errors: errors.append(str(error)))
                    try:
                        page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=self._config.timeout_ms)
                        try:
                            page.wait_for_load_state("networkidle", timeout=self._config.timeout_ms)
                        except Exception:
                            logger.debug("Browser networkidle timeout: viewport=%s", viewport.name)
                        metrics = page.evaluate("""() => ({ scrollWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0), viewportWidth: window.innerWidth, pageHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0) })""")
                        screenshot_path = None
                        if self._config.screenshot:
                            path = browser_dir / f"{viewport.name}.png"
                            page.screenshot(path=str(path), full_page=True)
                            screenshot_path = str(path)
                        results.append(BrowserViewportResult(name=viewport.name, width=viewport.width, height=viewport.height, horizontal_overflow=metrics["scrollWidth"] > metrics["viewportWidth"], scroll_width=metrics["scrollWidth"], viewport_width=metrics["viewportWidth"], page_height=metrics["pageHeight"], console_errors=console_errors, page_errors=page_errors, screenshot_path=screenshot_path))
                        logger.info("Browser viewport completed: name=%s overflow=%s console_errors=%d page_errors=%d", viewport.name, results[-1].horizontal_overflow, len(console_errors), len(page_errors))
                    finally:
                        context.close()
            finally:
                browser.close()
        if asset_root is not None:
            html_path.unlink(missing_ok=True)
        logger.info("Browser evaluation completed: iteration=%d", iteration)
        return BrowserEvaluationResult(enabled=True, viewports=results)
