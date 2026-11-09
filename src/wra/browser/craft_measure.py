from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from wra.browser.guard import guard_context
from wra.browser.models import BrowserCraftMeasurements, WrappedTextElement

logger = logging.getLogger(__name__)

CRAFT_VIEWPORT = {"width": 1280, "height": 800}
MAX_SELECTORS = 5
MAX_WRAPPED_LABELS = 8

_SELECTOR_HELPER = r"""
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${CSS.escape(el.id)}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).map(c => CSS.escape(c)).join('.')}`;
    return tag;
  }
"""

JS_OFF_SCRIPT = (
    "() => {"
    + _SELECTOR_HELPER
    + r"""
  document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} });
  const root = document.querySelector('main, [role=main]') || document.body;
  const styles = new Map();
  function styleOf(el) {
    let s = styles.get(el);
    if (!s) { s = getComputedStyle(el); styles.set(el, s); }
    return s;
  }
  function culprit(el) {
    let found = null;
    let fixed = false;
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const s = styleOf(e);
      if (s.display === 'none') return { none: true };
      if (s.position === 'fixed' || (s.pointerEvents === 'none' && s.position === 'absolute')) fixed = true;
      if (!found && (parseFloat(s.opacity) < 0.05 || (e === el && s.visibility === 'hidden'))) found = e;
    }
    return { none: false, found, fixed };
  }
  let total = 0;
  let hidden = 0;
  const selectors = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const parent = node.parentElement;
    if (!parent || parent.closest('script, style, template')) continue;
    const verdict = culprit(parent);
    if (verdict.none) continue;
    total += text.length;
    if (!verdict.found || verdict.fixed) continue;
    const box = verdict.found.getBoundingClientRect();
    if (box.width < 4 || box.height < 4) continue;
    hidden += text.length;
    const selector = selectorFor(verdict.found);
    if (selectors.length < 5 && !selectors.includes(selector)) selectors.push(selector);
  }
  return { total, hidden, selectors };
}"""
)

_CTA_HELPERS = r"""
  function shown(el) {
    const visibility = getComputedStyle(el).visibility;
    if (visibility === 'hidden' || visibility === 'collapse') return false;
    for (let e = el; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.display === 'none' || parseFloat(s.opacity) < 0.05) return false;
    }
    return true;
  }
  function labelOf(el) {
    const raw = el.tagName === 'INPUT' ? (el.value || '') : (el.textContent || '');
    return raw.replace(/\s+/g, ' ').trim();
  }
  function buttonish(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'button' || tag === 'input' || el.getAttribute('role') === 'button') return true;
    const s = getComputedStyle(el);
    if (s.backgroundColor !== 'rgba(0, 0, 0, 0)' && s.backgroundColor !== 'transparent') return true;
    if (s.backgroundImage !== 'none') return true;
    if (parseFloat(s.borderTopWidth) > 0 && s.borderTopStyle !== 'none' && parseFloat(s.borderLeftWidth) > 0 && s.borderLeftStyle !== 'none') return true;
    return /btn|button|cta/i.test(el.getAttribute('class') || '');
  }
  function heroHeading(h1) {
    return !!h1 && h1.getBoundingClientRect().top >= 120;
  }
  function candidates() {
    const out = [];
    for (const el of document.querySelectorAll('a, button, [role=button], input[type=submit]')) {
      if (el.closest('nav, [role=navigation]')) continue;
      const header = el.closest('header');
      if (header && !heroHeading(header.querySelector('h1'))) continue;
      if (labelOf(el).length < 2) continue;
      if (!buttonish(el) || !shown(el)) continue;
      out.push(el);
    }
    return out;
  }
"""

FIRST_SCREEN_HERO_SCRIPT = (
    "() => {"
    + _CTA_HELPERS
    + r"""
  function inFirstScreen(el) {
    const r = el.getBoundingClientRect();
    return r.width >= 4 && r.height >= 4 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  }
  const heading = Array.from(document.querySelectorAll('h1')).find(el => shown(el) && inFirstScreen(el));
  let cta = candidates().some(inFirstScreen);
  const zone = heroHeading(heading) ? heading.closest('section, header') : null;
  if (!cta && zone) {
    cta = Array.from(document.querySelectorAll('a[href]')).some(el => !el.closest('nav, [role=navigation], p, li') && el.closest('section, header') === zone && labelOf(el).length >= 2 && shown(el) && inFirstScreen(el));
  }
  return { h1: !!heading, cta };
}"""
)

BUTTON_WRAP_SCRIPT = (
    "() => {"
    + _SELECTOR_HELPER
    + _CTA_HELPERS
    + r"""
  const out = [];
  for (const el of candidates()) {
    if (out.length >= 8) break;
    const label = labelOf(el);
    if (label.length > 40) continue;
    const tops = [];
    const heights = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (!node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const rect of range.getClientRects()) {
        if (rect.width > 0 && rect.height > 0) { tops.push(rect.top); heights.push(rect.height); }
      }
    }
    if (!tops.length) continue;
    const lineHeight = Math.min(...heights);
    const sorted = tops.slice().sort((a, b) => a - b);
    let lines = 1;
    let lineTop = sorted[0];
    for (const top of sorted) {
      if (top > lineTop + lineHeight * 0.5) { lines++; lineTop = top; }
    }
    if (lines < 2) continue;
    out.push({ selector: selectorFor(el), text: label, line_count: lines, char_count: label.replace(/\s+/g, '').length });
  }
  return out;
}"""
)


def _run_scripts(browser, page_url: str, allowed_root: Path, timeout_ms: int, java_script_enabled: bool, scripts: dict[str, str], wait_idle: bool) -> dict[str, Any]:
    results: dict[str, Any] = {}
    context = None
    try:
        context = browser.new_context(viewport=CRAFT_VIEWPORT, java_script_enabled=java_script_enabled)
        guard_context(context, allowed_root)
        page = context.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_idle:
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                logger.debug("Craft measurement networkidle timeout")
        for name, script in scripts.items():
            try:
                results[name] = page.evaluate(script)
            except Exception as exc:
                logger.warning("Craft measurement failed: name=%s error=%s", name, exc)
    except Exception as exc:
        logger.warning("Craft measurement context failed: js=%s error=%s", java_script_enabled, exc)
    finally:
        if context is not None:
            context.close()
    return results


def measure_craft(browser, page_url: str, allowed_root: Path, timeout_ms: int) -> BrowserCraftMeasurements | None:
    js_off = _run_scripts(browser, page_url, allowed_root, timeout_ms, False, {"text": JS_OFF_SCRIPT}, False).get("text")
    live = _run_scripts(browser, page_url, allowed_root, timeout_ms, True, {"hero": FIRST_SCREEN_HERO_SCRIPT, "wrap": BUTTON_WRAP_SCRIPT}, True)
    hero = live.get("hero")
    if js_off is None and hero is None:
        return None
    return BrowserCraftMeasurements(
        js_off_text_chars=js_off["total"] if js_off else 0,
        js_off_hidden_chars=js_off["hidden"] if js_off else 0,
        js_off_hidden_selectors=js_off["selectors"][:MAX_SELECTORS] if js_off else [],
        first_screen_h1=bool(hero and hero["h1"]),
        first_screen_cta=bool(hero and hero["cta"]),
        wrapped_button_labels=[WrappedTextElement(**item) for item in (live.get("wrap") or [])[:MAX_WRAPPED_LABELS]],
    )
