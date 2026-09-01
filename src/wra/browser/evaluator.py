from pathlib import Path

from wra.browser.models import BrowserConfig, BrowserEvaluationResult, BrowserViewportResult


class BrowserEvaluator:
    """Render generated HTML in Chromium and collect deterministic browser evidence."""

    def __init__(self, config: BrowserConfig) -> None:
        self._config = config

    def evaluate(self, html: str, output_dir: str | Path, iteration: int) -> BrowserEvaluationResult:
        if not self._config.enabled:
            return BrowserEvaluationResult(enabled=False)
        if not self._config.viewports:
            raise ValueError("browser.viewports must contain at least one viewport when browser evaluation is enabled")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for browser evaluation. Install with: pip install 'wra[browser]'"
            ) from exc

        output_path = Path(output_dir)
        browser_dir = output_path / f"iteration_{iteration:03d}"
        browser_dir.mkdir(parents=True, exist_ok=True)
        html_path = browser_dir / "page.html"
        html_path.write_text(html, encoding="utf-8")
        results: list[BrowserViewportResult] = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for viewport in self._config.viewports:
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
                            pass
                        metrics = page.evaluate(
                            """
                            () => ({
                                scrollWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
                                viewportWidth: window.innerWidth,
                                pageHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0)
                            })
                            """
                        )
                        screenshot_path = None
                        if self._config.screenshot:
                            path = browser_dir / f"{viewport.name}.png"
                            page.screenshot(path=str(path), full_page=True)
                            screenshot_path = str(path)
                        results.append(BrowserViewportResult(
                            name=viewport.name,
                            width=viewport.width,
                            height=viewport.height,
                            horizontal_overflow=metrics["scrollWidth"] > metrics["viewportWidth"],
                            scroll_width=metrics["scrollWidth"],
                            viewport_width=metrics["viewportWidth"],
                            page_height=metrics["pageHeight"],
                            console_errors=console_errors,
                            page_errors=page_errors,
                            screenshot_path=screenshot_path,
                        ))
                    finally:
                        context.close()
            finally:
                browser.close()
        return BrowserEvaluationResult(enabled=True, viewports=results)
