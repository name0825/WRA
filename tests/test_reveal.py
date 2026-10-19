from types import SimpleNamespace

from wra.browser import reveal
from wra.browser.reveal import MAX_SCROLL_SECONDS, MAX_SCROLL_STEPS, reveal_animated_content


class FakePage:
    def __init__(self, height: int, viewport: int = 800, tick=None) -> None:
        self.height = height
        self.viewport = viewport
        self.tick = tick
        self.scrolls: list[int] = []
        self.waits: list[int] = []

    def evaluate(self, expression, argument=None):
        if "scrollHeight" in expression:
            return [self.height, self.viewport]
        if "scrollTo" in expression:
            self.scrolls.append(argument)
            return None
        return 3

    def wait_for_timeout(self, ms) -> None:
        self.waits.append(ms)
        if self.tick is not None:
            self.tick(ms)


def test_enormous_document_height_is_bounded_by_the_step_cap() -> None:
    page = FakePage(height=10**9)

    revealed = reveal_animated_content(page, 6000)

    assert revealed == 3
    assert len(page.scrolls) <= MAX_SCROLL_STEPS + 2
    assert len(page.waits) <= MAX_SCROLL_STEPS + 3
    assert page.scrolls[-1] == 0


def test_slow_waits_stop_at_the_time_bound_and_still_finish_the_reveal(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(reveal, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def tick(ms: int) -> None:
        clock[0] += MAX_SCROLL_SECONDS / 4

    page = FakePage(height=10**9, tick=tick)

    revealed = reveal_animated_content(page, 6000)

    assert revealed == 3
    assert len(page.scrolls) <= 4 + 2
    assert page.scrolls[-1] == 0
