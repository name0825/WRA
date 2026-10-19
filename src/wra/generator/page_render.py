import uuid
from contextlib import contextmanager
from pathlib import Path

from wra.browser.guard import guard_context


def new_guarded_page(browser, asset_root: str | Path | None):
    context = browser.new_context()
    guard_context(context, Path(asset_root).resolve() if asset_root is not None else None)
    return context.new_page()


@contextmanager
def page_loader(asset_root: str | Path | None):
    if asset_root is None:
        yield lambda page, html, **kwargs: page.set_content(html, **kwargs)
        return

    preview = Path(asset_root) / f".wra-repair-{uuid.uuid4().hex}.html"

    def load(page, html: str, **kwargs) -> None:
        preview.write_text(html, encoding="utf-8-sig")
        page.goto(preview.resolve().as_uri(), wait_until=kwargs.pop("wait_until", "load"), **kwargs)

    try:
        yield load
    finally:
        preview.unlink(missing_ok=True)
