import math
import time

MAX_SCROLL_STEPS = 60
MAX_SCROLL_SECONDS = 10.0

_PAGE_METRICS = "() => [document.documentElement.scrollHeight, window.innerHeight]"

_SCROLL_TO = "(top) => window.scrollTo({ top, left: 0, behavior: 'instant' })"

_REVEAL_REMAINING = """
() => {
  const freeze = document.createElement('style');
  freeze.textContent = '*,*::before,*::after{transition:none!important;animation:none!important}';
  document.head.appendChild(freeze);
  void document.body.offsetHeight;
  let revealed = 0;
  for (const element of document.body.querySelectorAll('*')) {
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (parseFloat(style.opacity) >= 0.05) continue;
    if (style.position === 'fixed' || (style.pointerEvents === 'none' && style.position === 'absolute')) continue;
    const box = element.getBoundingClientRect();
    if (box.width < 4 || box.height < 4) continue;
    element.style.setProperty('opacity', '1', 'important');
    element.style.setProperty('transform', 'none', 'important');
    element.style.setProperty('filter', 'none', 'important');
    revealed++;
  }
  return revealed;
}
"""


def reveal_animated_content(page, timeout_ms: int) -> int:
    delay = min(200, max(60, timeout_ms // 100))
    deadline = time.monotonic() + MAX_SCROLL_SECONDS
    height, viewport = page.evaluate(_PAGE_METRICS)
    step = max(viewport, math.ceil(height / MAX_SCROLL_STEPS), 1)
    offset = 0
    while offset < height and time.monotonic() < deadline:
        page.evaluate(_SCROLL_TO, offset)
        page.wait_for_timeout(delay)
        offset += step
    page.evaluate(_SCROLL_TO, height)
    page.wait_for_timeout(delay * 2)
    page.evaluate(_SCROLL_TO, 0)
    page.wait_for_timeout(delay)
    return page.evaluate(_REVEAL_REMAINING)
