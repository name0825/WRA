import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from wra.browser.evaluator import FRAMEWORK_CACHE_DIRNAME, HTTP_CACHE_DIRNAME, _localize_framework_cdns, _localize_google_fonts
from wra.browser.framework_cache import DEFAULT_CACHE_DIR
from wra.browser.guard import guard_context
from wra.browser.http_cache import DEFAULT_HTTP_CACHE_DIR
from wra.core.models import FrameworkConfig
from wra.utils.logging import get_logger

logger = get_logger(__name__)

_localization: tuple[FrameworkConfig, bool, Path, Path] | None = None


def configure_preview_localization(frameworks: FrameworkConfig | None, localize_web_fonts: bool, cache_dir: str | Path = DEFAULT_CACHE_DIR, http_cache_dir: str | Path = DEFAULT_HTTP_CACHE_DIR) -> None:
    global _localization
    _localization = None if frameworks is None else (frameworks, localize_web_fonts, Path(cache_dir), Path(http_cache_dir))


def new_guarded_page(browser, asset_root: str | Path | None):
    context = browser.new_context()
    guard_context(context, Path(asset_root).resolve() if asset_root is not None else None)
    return context.new_page()


@contextmanager
def page_loader(asset_root: str | Path | None):
    if asset_root is None:
        yield lambda page, html, **kwargs: page.set_content(html, **kwargs)
        return

    root = Path(asset_root)
    preview = root / f".wra-repair-{uuid.uuid4().hex}.html"
    framework_dirname = f"{FRAMEWORK_CACHE_DIRNAME}_{uuid.uuid4().hex}"
    http_dirname = f"{HTTP_CACHE_DIRNAME}_{uuid.uuid4().hex}"

    def load(page, html: str, **kwargs) -> None:
        rendered = html
        if _localization is not None:
            frameworks, localize_web_fonts, cache_dir, http_cache_dir = _localization
            try:
                rendered = _localize_framework_cdns(html, frameworks, cache_dir, root, framework_dirname)
                if localize_web_fonts:
                    rendered = _localize_google_fonts(rendered, http_cache_dir, root, http_dirname)
            except Exception as exc:
                logger.warning("Preview localization failed, rendering the unlocalized page: %s", exc)
                rendered = html
        preview.write_text(rendered, encoding="utf-8-sig")
        page.goto(preview.resolve().as_uri(), wait_until=kwargs.pop("wait_until", "load"), **kwargs)

    try:
        yield load
    finally:
        preview.unlink(missing_ok=True)
        shutil.rmtree(root / framework_dirname, ignore_errors=True)
        shutil.rmtree(root / http_dirname, ignore_errors=True)
