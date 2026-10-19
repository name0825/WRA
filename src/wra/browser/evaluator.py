from __future__ import annotations

import bisect
import re
import shutil
import tempfile
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING
import logging

from wra.browser.contrast import CONTRAST_CANDIDATES_SCRIPT, contrast_failures
from wra.browser.framework_cache import DEFAULT_CACHE_DIR, ensure_cached_path_for_url, iter_framework_urls
from wra.browser.guard import guard_context, launch_guarded
from wra.browser.http_cache import DEFAULT_HTTP_CACHE_DIR, fetch_cached
from wra.browser.models import BrowserConfig, BrowserEvaluationResult, BrowserViewportResult
from wra.browser.reveal import reveal_animated_content

if TYPE_CHECKING:
    from wra.core.models import FrameworkConfig

logger = logging.getLogger(__name__)

PREVIEW_NAME = ".browser_preview.html"
FRAMEWORK_CACHE_DIRNAME = "_frameworks_cache"
HTTP_CACHE_DIRNAME = "_http_cache"
GOOGLE_FONTS_CSS_PATTERN = re.compile(r"https://fonts\.googleapis\.com/css2?\?[^\s\"'<>)]+")
GSTATIC_FONT_URL_PATTERN = re.compile(r"url\(\s*['\"]?(https://fonts\.gstatic\.com/[^'\")\s]+)['\"]?\s*\)")
FONT_FACE_BLOCK_PATTERN = re.compile(r"@font-face\s*\{([^}]*)\}", re.IGNORECASE)
UNICODE_RANGE_PATTERN = re.compile(r"unicode-range\s*:\s*([^;]+);?", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^<>]*>")
_TEXT_ATTR_PATTERN = re.compile(
    r"""\b(?:alt|title|placeholder|aria-label)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE
)
BROKEN_IMAGES_SCRIPT = """() => {
  const out = [];
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc;
    if (!src || src.endsWith('.svg') || src.startsWith('data:image/svg')) continue;
    if (img.complete && img.naturalWidth === 0) out.push(src);
  }
  return [...new Set(out)].slice(0, 20);
}"""
# Walks top-down so a reported element's descendants are never re-reported
# (an overflowing wrapper already explains its overflowing children). Skips
# elements whose overflow is already contained by an ancestor's overflow-x
# clipping, and fixed-position drawers that are hidden or transformed fully
# offscreen -- both are intentionally offscreen, not layout bugs.
OVERFLOW_ELEMENTS_SCRIPT = """() => {
  const vw = window.innerWidth;
  const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0);
  if (scrollWidth <= vw) return [];
  const out = [];
  const seenTrapped = new Set();
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).join('.')}`;
    return tag;
  }
  function isClipped(el) {
    let node = el.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      const overflowX = getComputedStyle(node).overflowX;
      if (overflowX === 'hidden' || overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'clip') return true;
      node = node.parentElement;
    }
    return false;
  }
  function isOffscreenDrawer(el) {
    const style = getComputedStyle(el);
    if (style.position !== 'fixed') return false;
    if (style.visibility === 'hidden') return true;
    if (style.transform && style.transform !== 'none') {
      const rect = el.getBoundingClientRect();
      if (rect.right <= 0 || rect.left >= vw) return true;
    }
    return false;
  }
  function trapPropertyOf(style) {
    const backdrop = style.backdropFilter || style.webkitBackdropFilter;
    if (backdrop && backdrop !== 'none') return 'backdrop-filter';
    if (style.filter && style.filter !== 'none') return 'filter';
    if (style.transform && style.transform !== 'none') return 'transform';
    if (style.perspective && style.perspective !== 'none') return 'perspective';
    const contain = style.contain;
    if (contain && contain !== 'none' && /paint|layout|strict|content/.test(contain)) return 'contain';
    const willChange = style.willChange;
    if (willChange && /transform|filter|perspective|backdrop-filter/.test(willChange)) return 'will-change';
    return null;
  }
  function findTrappingAncestor(el) {
    let node = el.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      const prop = trapPropertyOf(getComputedStyle(node));
      if (prop) return { node, property: prop };
      node = node.parentElement;
    }
    return null;
  }
  function findFixedTrap(el) {
    let node = el;
    while (node && node !== document.body && node !== document.documentElement) {
      if (getComputedStyle(node).position === 'fixed') {
        const trap = findTrappingAncestor(node);
        if (trap) {
          return { node, trapped: true, selector: `${selectorFor(node)} (position: fixed trapped by ${selectorFor(trap.node)} ${trap.property})` };
        }
        return { node, trapped: false };
      }
      node = node.parentElement;
    }
    return null;
  }
  function containingBlockRect(el, pos) {
    if (pos === 'fixed') return { left: 0, width: vw };
    let node = el;
    while (node) {
      const p = getComputedStyle(node).position;
      if (p && p !== 'static') {
        const r = node.getBoundingClientRect();
        return { left: r.left, width: r.width };
      }
      node = node.parentElement;
    }
    return { left: 0, width: vw };
  }
  function translateXOf(transform) {
    if (!transform || transform === 'none') return 0;
    const m = transform.match(/^matrix\\(([^)]+)\\)$/);
    if (!m) return 0;
    const parts = m[1].split(',').map(s => parseFloat(s.trim()));
    return parts.length === 6 ? parts[4] : 0;
  }
  function pseudoOverflowRight(el, pseudo) {
    const style = getComputedStyle(el, pseudo);
    if (style.content === 'none' || style.display === 'none') return null;
    const pos = style.position;
    if (pos !== 'absolute' && pos !== 'fixed') return null;
    const cb = containingBlockRect(el, pos);
    const left = style.left, right = style.right, width = style.width;
    let l, w = width === 'auto' ? undefined : parseFloat(width);
    if (left !== 'auto') {
      l = cb.left + parseFloat(left);
      if (w === undefined && right !== 'auto') w = cb.width - parseFloat(left) - parseFloat(right);
    } else if (right !== 'auto' && w !== undefined) {
      l = cb.left + cb.width - parseFloat(right) - w;
    }
    if (l === undefined || w === undefined) return null;
    l += translateXOf(style.transform);
    return l + w;
  }
  function walk(el) {
    for (const child of el.children) {
      if (out.length >= 5) return;
      const rect = child.getBoundingClientRect();
      if (rect.right - vw > 1) {
        if (isClipped(child) || isOffscreenDrawer(child)) {
          walk(child);
          continue;
        }
        const trap = findFixedTrap(child);
        if (trap) {
          if (!trap.trapped) {
            walk(child);
            continue;
          }
          if (!seenTrapped.has(trap.node)) {
            seenTrapped.add(trap.node);
            out.push({ selector: trap.selector, right: Math.round(rect.right) });
          }
          continue;
        }
        out.push({ selector: selectorFor(child), right: Math.round(rect.right) });
        continue;
      }
      walk(child);
    }
  }
  walk(document.body);
  if (out.length === 0) {
    outer:
    for (const el of document.querySelectorAll('body, body *')) {
      for (const pseudo of ['::before', '::after']) {
        const right = pseudoOverflowRight(el, pseudo);
        if (right !== null && right - vw > 1) {
          out.push({ selector: `${selectorFor(el)}${pseudo}`, right: Math.round(right) });
          if (out.length >= 5) break outer;
        }
      }
    }
  }
  return out.slice(0, 5);
}"""
# Rightmost extent of visible text/image content whose vertical position
# intersects the first screen (the unscrolled viewport). A collapsed
# multi-column layout that pushes everything but a narrow sidebar below the
# fold leaves this far short of the viewport width; a normally laid-out page
# uses most of it.
FIRST_SCREEN_EXTENT_SCRIPT = """() => {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let maxRight = 0;
  const candidates = document.body.querySelectorAll('img, p, h1, h2, h3, h4, h5, h6, span, a, li, button, label, td, th, div, svg, canvas, video, iframe');
  for (const el of candidates) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const tag = el.tagName.toLowerCase();
    const isImg = tag === 'img';
    const isVisualTag = tag === 'svg' || tag === 'canvas' || tag === 'video' || tag === 'iframe';
    const hasBgImage = style.backgroundImage && style.backgroundImage !== 'none';
    if (!isImg && !isVisualTag && !hasBgImage) {
      if (el.children.length > 0) continue;
      const text = el.textContent.trim();
      if (text.length === 0) continue;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (rect.top >= vh || rect.bottom <= 0) continue;
    if (rect.right > maxRight) maxRight = rect.right;
  }
  return Math.round(maxRight);
}"""
# Visible text nodes (>=3 non-space chars) whose rendered line count is close
# to their character count: a nav item wrapped one letter per line, or an
# address box wrapped one word per line, both signs of a container squeezed
# far narrower than its content needs.
CHAR_WRAP_SCRIPT = """() => {
  const out = [];
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${CSS.escape(el.id)}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).map(c => CSS.escape(c)).join('.')}`;
    return tag;
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const text = node.textContent.trim();
      if (text.length < 3) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      const tag = parent.tagName.toLowerCase();
      if (['script', 'style', 'code', 'pre', 'textarea'].includes(tag)) return NodeFilter.FILTER_REJECT;
      const style = getComputedStyle(parent);
      if (style.display === 'none' || style.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
      const rect = parent.getBoundingClientRect();
      if (rect.right + window.scrollX <= 0 || rect.bottom + window.scrollY <= 0) return NodeFilter.FILTER_REJECT;
      if ((rect.width <= 1 || rect.height <= 1) && style.overflow !== 'visible') return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  let node;
  while ((node = walker.nextNode())) {
    if (out.length >= 8) break;
    const text = node.textContent.trim();
    const range = document.createRange();
    range.selectNodeContents(node);
    const lineCount = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0).length;
    if (lineCount < 2) continue;
    if (lineCount <= text.split(/\\s+/).length) continue;
    const charCount = text.replace(/\\s+/g, '').length;
    if ((charCount >= 8 && charCount / lineCount <= 4) || (charCount >= 3 && lineCount >= 3 && charCount / lineCount <= 1.5)) {
      out.push({ selector: selectorFor(node.parentElement), text: text.slice(0, 60), line_count: lineCount, char_count: charCount });
    }
  }
  return out;
}"""
# Gaps between merged content intervals (visible text, images, svg/canvas/
# video/iframe, or background-image boxes), measured over the whole document
# in page coordinates, not just the first screen. A band this tall between
# two sections is a hero or section whose content silently failed to render
# rather than a normal margin.
VERTICAL_GAP_SCRIPT = """() => {
  const intervals = [];
  function isVisible(style) {
    return style.display !== 'none' && style.visibility !== 'hidden';
  }
  function hasBgImageUrl(style) {
    return !!(style.backgroundImage && style.backgroundImage !== 'none' && style.backgroundImage.includes('url('));
  }
  if (hasBgImageUrl(getComputedStyle(document.body)) || hasBgImageUrl(getComputedStyle(document.documentElement))) {
    return [];
  }
  for (const el of document.body.querySelectorAll('*')) {
    const style = getComputedStyle(el);
    if (!isVisible(style)) continue;
    const tag = el.tagName.toLowerCase();
    const isVisualTag = tag === 'img' || tag === 'svg' || tag === 'canvas' || tag === 'video' || tag === 'iframe';
    const hasBgImage = style.backgroundImage && style.backgroundImage !== 'none';
    let hasOwnText = false;
    if (!isVisualTag && !hasBgImage) {
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE && node.textContent.trim().length > 0) {
          hasOwnText = true;
          break;
        }
      }
    }
    if (!isVisualTag && !hasBgImage && !hasOwnText) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const top = rect.top + window.scrollY;
    const bottom = rect.bottom + window.scrollY;
    if (bottom <= 0) continue;
    intervals.push([Math.max(top, 0), bottom]);
  }
  if (intervals.length === 0) return [];
  intervals.sort((a, b) => a[0] - b[0]);
  const merged = [intervals[0]];
  for (let i = 1; i < intervals.length; i++) {
    const [top, bottom] = intervals[i];
    const last = merged[merged.length - 1];
    if (top <= last[1]) {
      last[1] = Math.max(last[1], bottom);
    } else {
      merged.push([top, bottom]);
    }
  }
  const bands = [];
  for (let i = 1; i < merged.length; i++) {
    const top = merged[i - 1][1];
    const height = merged[i][0] - top;
    if (height > 0) bands.push({ top: Math.round(top), height: Math.round(height) });
  }
  bands.sort((a, b) => b.height - a.height);
  return bands.slice(0, 5);
}"""
# img/svg inside every site-level header-like container (header, nav,
# [role=banner] not inside article/footer/aside, and either within 400px of
# the page top or the first such container in the document) whose rendered
# size dwarfs a logo: taller than a reasonable logo cap, or spilling past the
# header box it sits in.
OVERSIZED_LOGO_FUNCTION = """function oversizedLogos() {
  const out = [];
  const seen = new Set();
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).join('.')}`;
    const alt = el.getAttribute && el.getAttribute('alt');
    if (alt) return `${tag}[alt="${alt.slice(0, 30)}"]`;
    return tag;
  }
  function isSiteHeader(container) {
    if (container.closest('article, footer, aside')) return false;
    if (container.getBoundingClientRect().top + window.scrollY <= 400) return true;
    const first = Array.from(document.querySelectorAll('header, nav, [role="banner"]')).find(node => !node.closest('article, footer, aside'));
    return first === container;
  }
  function inLogoContext(el) {
    let inHeader = false;
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      if (node.matches('header, nav, [role="banner"]') && isSiteHeader(node)) {
        if (node.tagName === 'NAV') return true;
        inHeader = true;
      }
    }
    if (!inHeader) return false;
    let node = el;
    for (let depth = 0; depth < 4 && node && node !== document.body; depth++, node = node.parentElement) {
      const name = `${node.id || ''} ${node.getAttribute('class') || ''} ${node.getAttribute('alt') || ''}`;
      if (/logo|brand/i.test(name)) return true;
      if (depth > 0 && node.tagName === 'A' && /^(\\/|\\.\\/|(\\.\\/)?index\\.html)$/.test(node.getAttribute('href') || '')) return true;
    }
    return false;
  }
  for (const header of document.querySelectorAll('header, nav, [role="banner"]')) {
    if (!isSiteHeader(header)) continue;
    const headerRect = header.getBoundingClientRect();
    for (const el of header.querySelectorAll('img, svg')) {
      if (seen.has(el)) continue;
      seen.add(el);
      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const inContext = inLogoContext(el);
      if (rect.width >= headerRect.width * 0.5 && !inContext) continue;
      const overshoot = rect.bottom - headerRect.bottom;
      if (overshoot > 24 || (inContext && rect.height > 160)) {
        out.push({ el, inContext, selector: selectorFor(el), width: Math.round(rect.width), height: Math.round(rect.height) });
      }
    }
  }
  return out;
}"""
HEADER_LOGO_SCRIPT = f"""() => {{
  {OVERSIZED_LOGO_FUNCTION}
  return oversizedLogos().slice(0, 5).map(({{ selector, width, height }}) => ({{ selector, width, height }}));
}}"""
# Text runs whose actual glyph line box (not the enclosing element's box, which
# a full-width block with centered text can leave at left=0) touches the
# viewport edge with no side padding.
EDGE_HUGGING_HITS_FUNCTION = """function edgeHuggingHits() {
  const vw = document.documentElement.clientWidth;
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).join('.')}`;
    return tag;
  }
  function isVisible(style) {
    return style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity) > 0.05;
  }
  function isHiddenAncestor(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.overflowX === 'auto' || style.overflowX === 'scroll' || style.position === 'fixed') return true;
      const rect = node.getBoundingClientRect();
      const clipsOverflow = [style.overflow, style.overflowX, style.overflowY].some(v => v === 'hidden' || v === 'clip');
      if ((rect.width <= 1 || rect.height <= 1) && (style.clip !== 'auto' || style.clipPath !== 'none' || clipsOverflow)) return true;
    }
    return false;
  }
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.trim();
    if (!text) continue;
    const el = node.parentElement;
    if (!el || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName) || !isVisible(getComputedStyle(el))) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    let hit = null;
    for (const rect of range.getClientRects()) {
      if (rect.width <= 0 || rect.height <= 0 || rect.bottom + window.scrollY <= 0) continue;
      if (rect.left >= 0 && rect.left < 4) { hit = rect; break; }
      if (rect.right <= vw && rect.right > vw - 4) { hit = rect; break; }
    }
    if (!hit || isHiddenAncestor(el)) continue;
    const section = el.closest('section, header, footer, nav, main');
    out.push({
      element: el,
      selector: selectorFor(el),
      section: section ? selectorFor(section) : '',
      left: Math.round(hit.left),
      right: Math.round(hit.right),
      text: text.slice(0, 30),
    });
    if (out.length >= 20) break;
  }
  return out;
}"""
EDGE_HUGGING_TEXT_SCRIPT = f"""() => {{
  {EDGE_HUGGING_HITS_FUNCTION}
  return edgeHuggingHits().map(({{ element, ...hit }}) => hit);
}}"""
# Pairs of header/nav elements (logo, nav links, buttons) whose rendered
# boxes overlap by at least 20% of the smaller box's area, a positioning
# mistake rather than an intentional layered design.
HEADER_OVERLAP_SCRIPT = """() => {
  const header = document.querySelector('header, nav, [role="banner"]');
  if (!header) return [];
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).join('.')}`;
    return tag;
  }
  function ownText(el) {
    let text = '';
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) text += child.textContent;
    }
    return text.trim();
  }
  function isVisible(style) {
    return style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity) > 0.05;
  }
  function isVisuallyHidden(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      if ((rect.width <= 1 || rect.height <= 1) && (style.clip !== 'auto' || style.clipPath !== 'none')) return true;
    }
    return false;
  }
  const candidates = [];
  for (const el of header.querySelectorAll('img, svg, a, button, span, li, div, h1, h2, h3, p')) {
    const style = getComputedStyle(el);
    if (!isVisible(style)) continue;
    const isMedia = el.tagName === 'IMG' || el.tagName === 'SVG';
    if (!isMedia && !ownText(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1 || rect.bottom <= 0) continue;
    if (isVisuallyHidden(el)) continue;
    candidates.push([el, rect]);
  }
  const out = [];
  for (let i = 0; i < candidates.length; i++) {
    for (let j = i + 1; j < candidates.length; j++) {
      const [a, rectA] = candidates[i];
      const [b, rectB] = candidates[j];
      if (a.contains(b) || b.contains(a)) continue;
      const width = Math.min(rectA.right, rectB.right) - Math.max(rectA.left, rectB.left);
      const height = Math.min(rectA.bottom, rectB.bottom) - Math.max(rectA.top, rectB.top);
      if (width <= 0 || height <= 0) continue;
      const ratio = (width * height) / Math.min(rectA.width * rectA.height, rectB.width * rectB.height);
      if (ratio < 0.2) continue;
      out.push({
        a: `${selectorFor(a)}:${(ownText(a) || a.tagName).slice(0, 12)}`,
        b: `${selectorFor(b)}:${(ownText(b) || b.tagName).slice(0, 12)}`,
        ratio: Math.round(ratio * 100) / 100,
      });
    }
  }
  return out.slice(0, 5);
}"""
# Visible text whose rendered box is painted over by another element that is
# neither its ancestor nor descendant, e.g. an absolutely positioned card
# stacked on top of a heading. elementFromPoint only hit-tests the currently
# visible viewport, so candidates below the fold are checked in scroll bands
# rather than against the page's full height at once. Fixed/sticky occluders
# are ignored once the page has scrolled (a pinned header sitting over
# scrolled-under content is normal and already covered by
# HEADER_OVERLAP_SCRIPT), but not at scrollY 0, where a fixed/sticky header
# painting over first-screen text is a real layout defect. Occluders with no
# paint are ignored too (elementFromPoint already skips pointer-events: none
# for us), as is text inside an absolutely/fixed positioned in-page skip
# link (a[href^="#"]), which is meant to sit under the header until focused.
TEXT_OCCLUSION_SCRIPT = """() => {
  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).slice(0, 2).join('.')}`;
    return tag;
  }
  function isVisible(style) {
    return style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity) > 0.05;
  }
  function isHiddenAncestor(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      if (rect.width <= 1 || rect.height <= 1) {
        if (style.clip !== 'auto' || style.clipPath !== 'none') return true;
        // A closed CSS-only accordion (max-height: 0; overflow: hidden) collapses
        // its panel to ~0px without display: none, but a text node inside still
        // reports its natural, uncropped rect -- not visible occlusion.
        if (style.overflow === 'hidden' || style.overflow === 'clip' || style.overflowX === 'hidden' || style.overflowX === 'clip' || style.overflowY === 'hidden' || style.overflowY === 'clip') return true;
      }
    }
    return false;
  }
  function isFixedOrSticky(el) {
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      const position = getComputedStyle(node).position;
      if (position === 'fixed' || position === 'sticky') return true;
    }
    return false;
  }
  function clipPathHidesBox(clipPath, rect) {
    if (clipPath === 'none') return false;
    if (/^(?:circle|ellipse)\\(\\s*0(?:px|%)?[\\s,)]/i.test(clipPath)) return true;
    const inset = clipPath.match(/^inset\\(([^)]*)\\)/i);
    if (!inset) return false;
    const offsets = inset[1].split(/\\s+round\\s+/i)[0].trim().split(/\\s+/).filter(Boolean);
    if (!offsets.length) return false;
    const toPx = (value, ref) => (value.endsWith('%') ? (parseFloat(value) / 100) * ref : parseFloat(value));
    let top, right, bottom, left;
    if (offsets.length === 1) { top = right = bottom = left = offsets[0]; }
    else if (offsets.length === 2) { top = bottom = offsets[0]; right = left = offsets[1]; }
    else if (offsets.length === 3) { top = offsets[0]; right = left = offsets[1]; bottom = offsets[2]; }
    else { [top, right, bottom, left] = offsets; }
    const vertical = toPx(top, rect.height) + toPx(bottom, rect.height);
    const horizontal = toPx(left, rect.width) + toPx(right, rect.width);
    return vertical >= rect.height - 0.5 || horizontal >= rect.width - 0.5;
  }
  function isSkipLink(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      if (node.tagName !== 'A' || !/^#/.test(node.getAttribute('href') || '')) continue;
      // An in-page anchor link is only a skip link if it is actually hidden at
      // rest by one of the common visually-hidden techniques -- a merely
      // absolutely/fixed positioned a[href^="#"] (e.g. a visible back-to-top
      // link, or one shaped with a non-collapsing clip-path) still needs to be
      // checked for real occlusion.
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      if (clipPathHidesBox(style.clipPath, rect) || style.clip !== 'auto') return true;
      if (rect.width <= 1 || rect.height <= 1) return true;
      if (style.position === 'absolute' || style.position === 'fixed') {
        const left = parseFloat(style.left);
        const top = parseFloat(style.top);
        if (left <= -999 || top <= -999) return true;
      }
      return false;
    }
    return false;
  }
  function isPainted(el) {
    const style = getComputedStyle(el);
    if (style.backgroundImage && style.backgroundImage !== 'none') return true;
    const match = style.backgroundColor.match(/rgba?\\(([^)]+)\\)/);
    if (match) {
      const parts = match[1].split(',').map(Number);
      if (parts.length < 4 || parts[3] > 0.05) return true;
    }
    if (['IMG', 'SVG', 'VIDEO', 'CANVAS'].includes(el.tagName)) return true;
    if (el.textContent && el.textContent.trim()) return true;
    return false;
  }
  const originalScrollY = window.scrollY;
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const pageHeight = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
  const candidates = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.trim();
    if (!text) continue;
    const el = node.parentElement;
    if (!el || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName) || !isVisible(getComputedStyle(el))) continue;
    if (isHiddenAncestor(el) || isFixedOrSticky(el) || isSkipLink(el)) continue;
    // A closed native <details> keeps its answer text's own computed style at
    // display: block (Chromium hides it via content-visibility: hidden on an
    // internal box instead), so it still lays out with a real rect at a real
    // document position -- checkVisibility() is the one API that sees through
    // that and reports it as not actually visible.
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility()) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    for (const rect of range.getClientRects()) {
      if (rect.width < 8 || rect.height < 4) continue;
      const docTop = rect.top + originalScrollY;
      if (docTop + rect.height <= 0 || docTop >= pageHeight) continue;
      candidates.push({ el, text, left: rect.left, width: rect.width, docTop, height: rect.height });
    }
    if (candidates.length >= 400) break;
  }
  const bands = new Map();
  for (const candidate of candidates) {
    const centerY = candidate.docTop + candidate.height / 2;
    const target = Math.max(0, Math.min(pageHeight - vh, centerY - vh / 2));
    const key = Math.round(target / 250) * 250;
    if (!bands.has(key)) bands.set(key, []);
    bands.get(key).push(candidate);
  }
  const out = [];
  for (const [scrollTarget, items] of bands) {
    if (out.length >= 20) break;
    window.scrollTo({ top: scrollTarget, left: 0, behavior: 'instant' });
    const actualScrollY = window.scrollY;
    for (const candidate of items) {
      if (out.length >= 20) break;
      const { el, text, left, width, docTop, height } = candidate;
      const viewportCenterY = docTop + height / 2 - actualScrollY;
      if (viewportCenterY < 0 || viewportCenterY > vh) continue;
      const xs = [left + width * 0.25, left + width * 0.5, left + width * 0.75].filter((x) => x >= 0 && x <= vw);
      if (!xs.length) continue;
      let hits = 0;
      let occluder = null;
      for (const x of xs) {
        const hitEl = document.elementFromPoint(x, viewportCenterY);
        if (!hitEl || hitEl === el || el.contains(hitEl) || hitEl.contains(el)) continue;
        if ((isFixedOrSticky(hitEl) && actualScrollY !== 0) || !isPainted(hitEl)) continue;
        hits++;
        if (!occluder) occluder = hitEl;
      }
      if (occluder && hits / xs.length >= 0.5) {
        out.push({ selector: selectorFor(el), text: text.slice(0, 30), occluder: selectorFor(occluder) });
      }
    }
  }
  window.scrollTo({ top: originalScrollY, left: 0, behavior: 'instant' });
  return out;
}"""
TEXT_WIDTH_VIEWPORT = {"width": 1920, "height": 1080}
TEXT_WIDTH_SCRIPT = """() => {
  const vw = window.innerWidth;
  const main = document.querySelector("main");
  const SKIP = new Set(["SCRIPT","STYLE","NOSCRIPT","TEMPLATE","LINK","META","BR"]);
  const CHROME = new Set(["HEADER","NAV","FOOTER"]);
  const kids = (el) => Array.from(el.children).filter(c => !SKIP.has(c.tagName) && c.getBoundingClientRect().height > 0);
  let root = main || document.body;
  let secs = kids(root);
  let guard = 0;
  while (secs.length === 1 && guard < 4 && kids(secs[0]).length > 1) { root = secs[0]; secs = kids(root); guard++; }
  if (main) {
    for (const c of kids(document.body)) {
      if (c !== main && !c.contains(main) && !main.contains(c) && !CHROME.has(c.tagName)) secs.push(c);
    }
  }
  secs = secs.filter(c => !CHROME.has(c.tagName));
  const exCache = new Map();
  const excluded = (el) => {
    if (!el || el === document.documentElement) return false;
    if (exCache.has(el)) return exCache.get(el);
    let v = false;
    const t = el.tagName;
    if (t === "NAV") v = true;
    else if ((t === "HEADER" || t === "FOOTER") && !el.parentElement.closest("section,article,main")) v = true;
    else {
      const pos = getComputedStyle(el).position;
      if (pos === "fixed" || pos === "sticky") v = true;
    }
    if (!v) v = excluded(el.parentElement);
    exCache.set(el, v);
    return v;
  };
  const widths = [];
  for (const sec of secs) {
    if (excluded(sec)) continue;
    let l = Infinity, r = -Infinity, n = 0;
    const take = (x0, x1, h) => {
      const a = Math.max(0, x0), b = Math.min(vw, x1);
      if (b - a < 1 || h < 1) return;
      if (a < l) l = a;
      if (b > r) r = b;
      n++;
    };
    const tw = document.createTreeWalker(sec, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = tw.nextNode())) {
      if (!node.nodeValue || !node.nodeValue.trim()) continue;
      const p = node.parentElement;
      if (!p || SKIP.has(p.tagName) || p.closest("svg,select,option")) continue;
      if (excluded(p)) continue;
      if (getComputedStyle(p).visibility === "hidden") continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const rc of range.getClientRects()) take(rc.left, rc.right, rc.height);
    }
    for (const im of sec.querySelectorAll("img,svg,video,canvas,iframe")) {
      if (im.parentElement && im.parentElement.closest("svg")) continue;
      if (excluded(im)) continue;
      if (getComputedStyle(im).visibility === "hidden") continue;
      const rc = im.getBoundingClientRect();
      if (rc.width < 1 || rc.width >= 0.95 * vw) continue;
      take(rc.left, rc.right, rc.height);
    }
    const h = Math.round(sec.getBoundingClientRect().height);
    if (n > 0 && h >= 150) widths.push(Math.round(r) - Math.round(l));
  }
  widths.sort((a, b) => a - b);
  if (!widths.length) return null;
  return widths.length % 2 ? widths[(widths.length - 1) / 2] : (widths[widths.length / 2 - 1] + widths[widths.length / 2]) / 2;
}"""
# Bounds on a single page's font fan-out: an LLM page can otherwise emit
# thousands of distinct family URLs, each triggering its own HTTPS fetch and
# cache write with no expiry (CWE-770). A Chrome UA gets Korean families back
# as ~100 unicode-range slices per weight; after filtering to slices that
# intersect the page's actual text, a measured real page (Noto Sans KR x3
# weights + Noto Serif KR x2 weights) needed only 28 slices / ~1.27MB, so
# these bounds (already headroom above that) are left unchanged.
MAX_CSS_URLS_PER_PAGE = 8
MAX_FONT_URLS_PER_PAGE = 64
MAX_TOTAL_BYTES_PER_PAGE = 20 * 1024 * 1024
# _TAG_PATTERN is now bounded to a single tag per match (`<[^<>]*>`), so it
# can no longer backtrack across the whole document; MAX_VISIBLE_TEXT_CHARS
# is kept only as a secondary bound on total work (CWE-1333).
MAX_VISIBLE_TEXT_CHARS = 2_000_000
# A lowercased copy was dropped: some characters (e.g. 'İ') expand under
# .lower(), which shifts offsets out of sync with the original string and
# can leak script/style content. Search the original string directly with
# case-insensitive regexes instead.
_SCRIPT_STYLE_OPEN = re.compile(r"<(script|style)(?![\w:-])", re.IGNORECASE)
_SCRIPT_CLOSE = re.compile(r"</script\s*>", re.IGNORECASE)
_STYLE_CLOSE = re.compile(r"</style\s*>", re.IGNORECASE)


def _strip_script_style(html: str) -> str:
    """Remove ``<script>``/``<style>`` element content in linear time.

    Equivalent in effect to the old backtracking regex strip, but ``pos``
    only ever moves forward, so no input can force quadratic work
    (CWE-1333). If a script/style opener has no matching close tag, the rest
    of the input is dropped rather than treated as visible text.
    """
    length = len(html)
    out: list[str] = []
    pos = 0
    while pos < length:
        open_match = _SCRIPT_STYLE_OPEN.search(html, pos)
        if open_match is None:
            out.append(html[pos:])
            break
        start = open_match.start()
        name = open_match.group(1).lower()
        out.append(html[pos:start])
        close_pattern = _SCRIPT_CLOSE if name == "script" else _STYLE_CLOSE
        close_match = close_pattern.search(html, open_match.end())
        if close_match is None:
            break
        out.append(" ")
        pos = close_match.end()
    return "".join(out)


def _visible_text_codepoints(html: str) -> set[int]:
    """Code points a browser could actually need to render from this page.

    Used to decide which Google Fonts unicode-range slices are worth
    fetching. Strips ``<script>``/``<style>`` content (never rendered text),
    then strips remaining tags but keeps their text content, plus
    alt/title/placeholder/aria-label attribute text.
    """
    html = html[:MAX_VISIBLE_TEXT_CHARS]
    without_script_style = _strip_script_style(html)
    attr_text = "".join(
        group for match in _TEXT_ATTR_PATTERN.findall(without_script_style) for group in match
    )
    visible_text = _TAG_PATTERN.sub(" ", without_script_style)
    combined = unescape(visible_text) + unescape(attr_text)
    return {ord(ch) for ch in combined}


def _parse_unicode_range(range_text: str) -> list[tuple[int, int]]:
    """Parse a CSS ``unicode-range`` value into inclusive (start, end) pairs.

    Handles comma-separated tokens in ``U+XXXX``, ``U+XXXX-YYYY`` (range) and
    ``U+XX??`` (wildcard) forms.
    """
    ranges: list[tuple[int, int]] = []
    for token in range_text.split(","):
        token = token.strip()
        if not token.upper().startswith("U+"):
            continue
        body = token[2:]
        try:
            if "-" in body:
                start_str, end_str = body.split("-", 1)
                ranges.append((int(start_str, 16), int(end_str, 16)))
            elif "?" in body:
                ranges.append((int(body.replace("?", "0"), 16), int(body.replace("?", "F"), 16)))
            else:
                value = int(body, 16)
                ranges.append((value, value))
        except ValueError:
            continue
    return ranges


def _unicode_range_matches(range_text: str, sorted_points: list[int]) -> bool:
    if not sorted_points:
        return False
    count = len(sorted_points)
    for start, end in _parse_unicode_range(range_text):
        index = bisect.bisect_left(sorted_points, start)
        if index < count and sorted_points[index] <= end:
            return True
    return False


def _iter_font_face_urls(css_text: str, sorted_page_points: list[int]):
    """Yield gstatic font URLs from ``@font-face`` blocks worth localizing.

    A block with no ``unicode-range`` is always yielded (it matches every
    character). A block with one is only yielded when its range intersects
    the page's visible text: a Korean family can otherwise split into 100+
    per-weight slices the page never actually uses.
    """
    for block_match in FONT_FACE_BLOCK_PATTERN.finditer(css_text):
        block = block_match.group(1)
        urls = GSTATIC_FONT_URL_PATTERN.findall(block)
        if not urls:
            continue
        range_match = UNICODE_RANGE_PATTERN.search(block)
        if range_match is None or _unicode_range_matches(range_match.group(1), sorted_page_points):
            yield from urls


def _localize_framework_cdns(html: str, frameworks: FrameworkConfig, cache_dir: Path, dest_dir: Path, cache_dirname: str = FRAMEWORK_CACHE_DIRNAME) -> str:
    """Rewrite catalog CDN URLs the page references to a locally cached copy.

    A URL with no prefetched copy (see scripts/prefetch_frameworks.py) is
    downloaded on the fly; a download failure raises so a broken render is
    never silently evaluated as a real one.
    """
    cache_root = dest_dir / cache_dirname
    for url, extension in iter_framework_urls(frameworks):
        if url not in html:
            continue
        cached = ensure_cached_path_for_url(url, extension, cache_dir)
        cache_root.mkdir(parents=True, exist_ok=True)
        local_path = cache_root / cached.name
        if not local_path.is_file():
            shutil.copyfile(cached, local_path)
        html = html.replace(url, f"{cache_dirname}/{cached.name}")
    return html


_SCRIPT_TAG_PATTERN = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_LINK_TAG_PATTERN = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_PATTERN = re.compile(r'\bsrc\s*=\s*["\'](https?://[^"\']+)["\']', re.IGNORECASE)
_HREF_ATTR_PATTERN = re.compile(r'\bhref\s*=\s*["\'](https?://[^"\']+)["\']', re.IGNORECASE)
_REL_STYLESHEET_PATTERN = re.compile(r'\brel\s*=\s*["\']stylesheet["\']', re.IGNORECASE)
_LOCALIZED_FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


def _find_blocked_external_resources(html: str) -> list[str]:
    """List external http(s) script/stylesheet references left unresolved after localization.

    These are still aborted by the sandbox's route handler, so the page
    renders without them; recorded here so that block is visible instead of
    scoring a broken render as a real one. Google Fonts URLs are excluded:
    the fetch-once path above already handles those, including its own
    silent fallback on a fetch failure.
    """
    urls: list[str] = []
    for tag in _SCRIPT_TAG_PATTERN.findall(html):
        match = _SRC_ATTR_PATTERN.search(tag)
        if match:
            urls.append(match.group(1))
    for tag in _LINK_TAG_PATTERN.findall(html):
        if not _REL_STYLESHEET_PATTERN.search(tag):
            continue
        match = _HREF_ATTR_PATTERN.search(tag)
        if match:
            urls.append(match.group(1))
    return [url for url in dict.fromkeys(urls) if not any(host in url for host in _LOCALIZED_FONT_HOSTS)]


def _localize_google_fonts(html: str, http_cache_dir: Path, dest_dir: Path, cache_dirname: str = HTTP_CACHE_DIRNAME) -> str:
    """Fetch-once and rewrite Google Fonts CSS/font URLs the page references.

    Only https URLs on the fonts.googleapis.com/fonts.gstatic.com allowlist
    (see http_cache.ALLOWED_HOSTS) are ever fetched, and a fetch failure never
    raises; anything not resolved from the cache is left as-is, so the route
    handler still aborts it exactly like today.
    """
    cache_root = dest_dir / cache_dirname
    css_matches = list(dict.fromkeys(GOOGLE_FONTS_CSS_PATTERN.findall(html)))
    if not css_matches:
        return html
    if len(css_matches) > MAX_CSS_URLS_PER_PAGE:
        logger.warning("Google Fonts CSS URL count exceeds per-page cap, only localizing the first %d: found=%d", MAX_CSS_URLS_PER_PAGE, len(css_matches))

    sorted_page_points = sorted(_visible_text_codepoints(html))
    total_bytes = 0
    total_fonts = 0
    budget_exceeded = False
    for match in css_matches[:MAX_CSS_URLS_PER_PAGE]:
        if budget_exceeded:
            break
        css_url = unescape(match)
        cached_css = fetch_cached(css_url, http_cache_dir)
        if cached_css is None:
            continue
        css_size = cached_css.stat().st_size
        if total_bytes + css_size > MAX_TOTAL_BYTES_PER_PAGE:
            logger.warning("Google Fonts per-page byte budget exceeded, stopping localization: url=%s", css_url)
            break
        total_bytes += css_size
        try:
            css_text = cached_css.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        font_matches = list(dict.fromkeys(_iter_font_face_urls(css_text, sorted_page_points)))
        for font_url in font_matches:
            if total_fonts >= MAX_FONT_URLS_PER_PAGE:
                logger.warning("Google Fonts font URL count exceeds per-page cap, stopping localization: cap=%d", MAX_FONT_URLS_PER_PAGE)
                break
            cached_font = fetch_cached(font_url, http_cache_dir)
            if cached_font is None:
                continue
            font_size = cached_font.stat().st_size
            if total_bytes + font_size > MAX_TOTAL_BYTES_PER_PAGE:
                logger.warning("Google Fonts per-page byte budget exceeded, stopping localization: url=%s", font_url)
                budget_exceeded = True
                break
            total_bytes += font_size
            total_fonts += 1
            cache_root.mkdir(parents=True, exist_ok=True)
            local_font = cache_root / cached_font.name
            if not local_font.is_file():
                shutil.copyfile(cached_font, local_font)
            # The rewritten CSS file itself lives inside cache_root, so a
            # font url() inside it resolves relative to cache_root, not to
            # the HTML page; prefixing it with cache_dirname here would
            # double that path segment and 404 every slice.
            css_text = css_text.replace(font_url, cached_font.name)
        cache_root.mkdir(parents=True, exist_ok=True)
        (cache_root / cached_css.name).write_text(css_text, encoding="utf-8")
        html = html.replace(match, f"{cache_dirname}/{cached_css.name}")
    return html


class BrowserEvaluator:
    """Render generated HTML in Chromium and collect deterministic browser evidence."""

    def __init__(
        self,
        config: BrowserConfig,
        frameworks: FrameworkConfig | None = None,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        http_cache_dir: str | Path = DEFAULT_HTTP_CACHE_DIR,
    ) -> None:
        self._config = config
        self._frameworks = frameworks
        self._cache_dir = Path(cache_dir)
        self._http_cache_dir = Path(http_cache_dir)

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
        owned_cache_dirs: list[Path] = []
        framework_dirname = FRAMEWORK_CACHE_DIRNAME
        http_dirname = HTTP_CACHE_DIRNAME
        results: list[BrowserViewportResult] = []
        failed_viewports: list[str] = []
        wide_text_width_median: int | None = None
        text_width_measured = False
        last_error: Exception | None = None

        try:
            if asset_root is not None:
                unique_names = []
                for base_name in (FRAMEWORK_CACHE_DIRNAME, HTTP_CACHE_DIRNAME):
                    unique_dir = Path(tempfile.mkdtemp(prefix=f"{base_name}_", dir=page_root))
                    owned_cache_dirs.append(unique_dir)
                    unique_names.append(unique_dir.name)
                framework_dirname, http_dirname = unique_names
            browser_html = _localize_framework_cdns(html, self._frameworks, self._cache_dir, page_root, framework_dirname) if self._frameworks is not None else html
            if self._config.localize_web_fonts:
                browser_html = _localize_google_fonts(browser_html, self._http_cache_dir, page_root, http_dirname)
            blocked_external_resources = _find_blocked_external_resources(browser_html)
            if blocked_external_resources:
                logger.warning("Browser evaluation is blocking uncataloged external resources: urls=%s", blocked_external_resources)
            html_path.write_text(browser_html, encoding="utf-8")
            allowed_root = page_root.resolve()
            with sync_playwright() as playwright:
                browser = launch_guarded(playwright)
                try:
                    for viewport in self._config.viewports:
                        logger.info("Browser viewport started: name=%s size=%dx%d", viewport.name, viewport.width, viewport.height)
                        console_errors: list[str] = []
                        page_errors: list[str] = []
                        context = browser.new_context(viewport={"width": viewport.width, "height": viewport.height})
                        guard_context(context, allowed_root)
                        page = context.new_page()
                        page.on("console", lambda message, errors=console_errors: errors.append(message.text) if message.type == "error" else None)
                        page.on("pageerror", lambda error, errors=page_errors: errors.append(str(error)))
                        try:
                            try:
                                page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=self._config.timeout_ms)
                                try:
                                    page.wait_for_load_state("networkidle", timeout=self._config.timeout_ms)
                                except Exception:
                                    logger.debug("Browser networkidle timeout: viewport=%s", viewport.name)
                                screenshot_path = None
                                if self._config.screenshot:
                                    path = browser_dir / f"{viewport.name}.png"
                                    try:
                                        revealed = reveal_animated_content(page, self._config.timeout_ms)
                                        logger.info("Browser viewport reveal pass: name=%s forced_visible=%d", viewport.name, revealed)
                                    except Exception as exc:
                                        logger.warning("Browser viewport reveal pass failed: name=%s error=%s", viewport.name, exc)
                                    page.screenshot(path=str(path), full_page=True)
                                    screenshot_path = str(path)
                                metrics = page.evaluate("""() => { let cssRuleCount = 0; for (const sheet of document.styleSheets) { try { cssRuleCount += sheet.cssRules.length; } catch (e) { cssRuleCount = null; break; } } return { scrollWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0), viewportWidth: window.innerWidth, pageHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0), cssRuleCount }; }""")
                                try:
                                    failures = contrast_failures(page.evaluate(CONTRAST_CANDIDATES_SCRIPT))
                                except Exception as exc:
                                    logger.warning("Browser viewport contrast check failed: name=%s error=%s", viewport.name, exc)
                                    failures = []
                                try:
                                    broken_images = page.evaluate(BROKEN_IMAGES_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport broken-image check failed: name=%s error=%s", viewport.name, exc)
                                    broken_images = []
                                try:
                                    overflow_elements = page.evaluate(OVERFLOW_ELEMENTS_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport overflow-element check failed: name=%s error=%s", viewport.name, exc)
                                    overflow_elements = []
                                try:
                                    first_screen_max_right = page.evaluate(FIRST_SCREEN_EXTENT_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport first-screen-extent check failed: name=%s error=%s", viewport.name, exc)
                                    first_screen_max_right = None
                                try:
                                    wrapped_text_elements = page.evaluate(CHAR_WRAP_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport char-wrap check failed: name=%s error=%s", viewport.name, exc)
                                    wrapped_text_elements = []
                                try:
                                    vertical_gap_bands = page.evaluate(VERTICAL_GAP_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport vertical-gap check failed: name=%s error=%s", viewport.name, exc)
                                    vertical_gap_bands = []
                                try:
                                    header_logo_elements = page.evaluate(HEADER_LOGO_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport header-logo check failed: name=%s error=%s", viewport.name, exc)
                                    header_logo_elements = []
                                try:
                                    edge_hugging_text_elements = page.evaluate(EDGE_HUGGING_TEXT_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport edge-hugging-text check failed: name=%s error=%s", viewport.name, exc)
                                    edge_hugging_text_elements = []
                                try:
                                    header_overlap_pairs = page.evaluate(HEADER_OVERLAP_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport header-overlap check failed: name=%s error=%s", viewport.name, exc)
                                    header_overlap_pairs = []
                                try:
                                    occluded_text_elements = page.evaluate(TEXT_OCCLUSION_SCRIPT)
                                except Exception as exc:
                                    logger.warning("Browser viewport text-occlusion check failed: name=%s error=%s", viewport.name, exc)
                                    occluded_text_elements = []
                                results.append(BrowserViewportResult(name=viewport.name, width=viewport.width, height=viewport.height, horizontal_overflow=metrics["scrollWidth"] > metrics["viewportWidth"], scroll_width=metrics["scrollWidth"], viewport_width=metrics["viewportWidth"], page_height=metrics["pageHeight"], console_errors=console_errors, page_errors=page_errors, screenshot_path=screenshot_path, stylesheet_rule_count=metrics["cssRuleCount"], contrast_failures=failures, broken_images=broken_images, overflow_elements=overflow_elements, first_screen_max_right=first_screen_max_right, wrapped_text_elements=wrapped_text_elements, vertical_gap_bands=vertical_gap_bands, header_logo_elements=header_logo_elements, edge_hugging_text_elements=edge_hugging_text_elements, header_overlap_pairs=header_overlap_pairs, occluded_text_elements=occluded_text_elements))
                                logger.info("Browser viewport completed: name=%s overflow=%s console_errors=%d page_errors=%d", viewport.name, results[-1].horizontal_overflow, len(console_errors), len(page_errors))
                                if not text_width_measured:
                                    text_width_measured = True
                                    try:
                                        page.set_viewport_size(TEXT_WIDTH_VIEWPORT)
                                        page.wait_for_timeout(250)
                                        median = page.evaluate(TEXT_WIDTH_SCRIPT)
                                        wide_text_width_median = None if median is None else round(median)
                                    except Exception as exc:
                                        logger.warning("Browser wide text width check failed: error=%s", exc)
                            except Exception as exc:
                                last_error = exc
                                failed_viewports.append(viewport.name)
                                logger.warning("Browser viewport failed: name=%s error=%s", viewport.name, exc)
                        finally:
                            context.close()
                finally:
                    browser.close()
        finally:
            if asset_root is not None:
                html_path.unlink(missing_ok=True)
                for cache_path in owned_cache_dirs:
                    shutil.rmtree(cache_path, ignore_errors=True)
        if not results and last_error is not None:
            raise last_error
        logger.info("Browser evaluation completed: iteration=%d wide_text_width_median=%s", iteration, wide_text_width_median)
        return BrowserEvaluationResult(enabled=True, viewports=results, failed_viewports=failed_viewports, blocked_external_resources=blocked_external_resources, wide_text_width_median=wide_text_width_median)
