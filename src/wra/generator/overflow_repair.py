import logging
from collections import Counter
from pathlib import Path

from .fixed_trap_repair import _VIEWPORTS, _inject_style
from wra.browser.guard import launch_guarded
from .page_render import new_guarded_page, page_loader

logger = logging.getLogger(__name__)

_SELECTOR_HELPERS = """  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${CSS.escape(el.id)}`;
    if (el.classList.length) return `${tag}.${Array.from(el.classList).map(c => CSS.escape(c)).join('.')}`;
    return tag;
  }
  function uniqueSelector(el) {
    let selector = selectorFor(el);
    let node = el.parentElement;
    for (let depth = 0; depth < 4; depth++) {
      if (document.querySelectorAll(selector).length === 1) return selector;
      if (!node || node === document.documentElement) return null;
      selector = `${selectorFor(node)} > ${selector}`;
      node = node.parentElement;
    }
    return null;
  }
  function positionalSelector(el) {
    const parts = [];
    let anchored = false;
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const anchor = node === el ? null : uniqueSelector(node);
      if (anchor) {
        parts.unshift(anchor);
        anchored = true;
        break;
      }
      parts.unshift(`${node.tagName.toLowerCase()}:nth-child(${Array.from(node.parentElement.children).indexOf(node) + 1})`);
    }
    if (!anchored) parts.unshift('body');
    const selector = parts.join(' > ');
    return document.querySelectorAll(selector).length === 1 ? selector : null;
  }"""

# Walks the page like the evaluator's OVERFLOW_ELEMENTS_SCRIPT (skipping
# elements clipped by an ancestor's overflow-x and anything inside a fixed
# element, which the fixed-trap repair owns) and classifies each outermost
# element that pushes past the right edge:
#   offcanvas    -- absolutely positioned and parked entirely past the right edge
#   reveal       -- translated horizontally while not yet revealed (opacity < 1)
#   gutter       -- negative horizontal margins the parent's padding doesn't absorb
#   navwrap      -- in-flow flex row with nowrap whose children don't fit (the
#                   offender's ancestor, or the overflowing row inside it)
#   gridcollapse -- grid with multiple explicit columns whose tracks don't fit
#   unscale      -- scaled up by its own transform with an untransformed box that
#                   fits the viewport
#   media        -- img/video inside the offender whose own box passes the right
#                   edge without a percentage max-width
# Anything else is reported as an offender but left unclassified.
_DETECT_SCRIPT = """() => {
  const vw = window.innerWidth;
  const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0);
  const result = { viewport: vw, scrollWidth, offenders: [], candidates: [] };
  if (scrollWidth <= vw) return result;
__SELECTOR_HELPERS__
  function isClipped(el) {
    let node = el.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      const overflowX = getComputedStyle(node).overflowX;
      if (overflowX === 'hidden' || overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'clip') return true;
      node = node.parentElement;
    }
    return false;
  }
  function inFixed(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      if (getComputedStyle(node).position === 'fixed') return true;
    }
    return false;
  }
  function spansViewport(node) {
    const r = node.getBoundingClientRect();
    return r.left <= 1 && r.right >= vw - 1 && r.right <= vw + 1;
  }
  function translateXOf(style) {
    let x = 0;
    const t = style.transform;
    if (t && t !== 'none') {
      const m = t.match(/^matrix(3d)?\\(([^)]+)\\)$/);
      if (m) {
        const p = m[2].split(',').map(s => parseFloat(s.trim()));
        x += (m[1] ? p[12] : p[4]) || 0;
      }
    }
    if (style.translate && style.translate !== 'none') x += parseFloat(style.translate) || 0;
    return x;
  }
  function isValidContainer(node, el, depth) {
    if (depth > 3) return false;
    if (node.tagName === 'BODY' || node.tagName === 'MAIN') return false;
    const parent = node.parentElement;
    if (parent && (parent.tagName === 'BODY' || parent.tagName === 'MAIN')) return false;
    if (node === el) return true;
    const r = node.getBoundingClientRect();
    return r.right > vw || node.scrollWidth > node.clientWidth;
  }
  function flexNowrapContainer(el) {
    let depth = 0;
    for (let node = el; node && node !== document.body; node = node.parentElement, depth++) {
      const s = getComputedStyle(node);
      if (s.display !== 'flex' && s.display !== 'inline-flex') continue;
      if (s.flexWrap !== 'nowrap' || !/^row/.test(s.flexDirection)) continue;
      if (s.position === 'fixed' || s.position === 'absolute' || s.position === 'sticky') continue;
      if (!isValidContainer(node, el, depth)) continue;
      return node;
    }
    return null;
  }
  function flexNowrapInside(el) {
    const found = [];
    for (const node of [el, ...el.querySelectorAll('*')]) {
      const s = getComputedStyle(node);
      if (s.display !== 'flex' && s.display !== 'inline-flex') continue;
      if (s.flexWrap !== 'nowrap' || !/^row/.test(s.flexDirection) || [...node.childNodes].filter(n => n.nodeType === 1 || (n.nodeType === 3 && n.textContent.trim())).length < 2) continue;
      if (s.position === 'fixed' || s.position === 'absolute' || s.position === 'sticky') continue;
      if (s.overflowX !== 'visible' || isClipped(node) || inFixed(node)) continue;
      const right = node.getBoundingClientRect().right;
      if (right > vw + 1 || node.scrollWidth > node.clientWidth + 1) found.push({ node, right });
    }
    const past = found.filter(f => f.right > vw + 1);
    const pool = past.length ? past : found;
    const innermost = pool.filter(f => !pool.some(other => other !== f && f.node.contains(other.node)));
    innermost.sort((a, b) => b.right - a.right);
    return innermost.length ? innermost[0].node : null;
  }
  function gridMultiColumnContainer(el) {
    let depth = 0;
    let own = null;
    for (let node = el; node && node !== document.body; node = node.parentElement, depth++) {
      const s = getComputedStyle(node);
      if (s.display !== 'grid' && s.display !== 'inline-grid') continue;
      if (s.position === 'fixed' || s.position === 'absolute' || s.position === 'sticky') continue;
      const raw = s.gridTemplateColumns.trim();
      if (!raw || raw === 'none' || /^subgrid/.test(raw)) continue;
      const cols = raw.replace(/\\[[^\\]]*\\]/g, '').trim().split(/\\s+/).filter(Boolean);
      if (cols.length <= 1) continue;
      if (!isValidContainer(node, el, depth)) continue;
      if (node === el && node.scrollWidth <= node.clientWidth) {
        own = node;
        continue;
      }
      return { node, own };
    }
    return own && { node: own, own: null };
  }
  function gridFitTracks(node) {
    const s = getComputedStyle(node);
    const tracks = s.gridTemplateColumns.replace(/\\[[^\\]]*\\]/g, '').trim().split(/\\s+/).map(parseFloat);
    if (tracks.some(t => !(t > 0))) return null;
    const box = node.getBoundingClientRect().width
      - (parseFloat(s.paddingLeft) || 0) - (parseFloat(s.paddingRight) || 0)
      - (parseFloat(s.borderLeftWidth) || 0) - (parseFloat(s.borderRightWidth) || 0);
    const sum = tracks.reduce((a, b) => a + b, 0);
    const gaps = (parseFloat(s.columnGap) || 0) * (tracks.length - 1);
    if (sum > box + 1 || sum + gaps <= box + 1) return null;
    return tracks.map(t => Math.round(t * 10) / 10);
  }
  function gridCollapseCandidate(el, label) {
    const gridContainer = gridMultiColumnContainer(el);
    if (!gridContainer) return null;
    const up = uniqueSelector(gridContainer.node);
    const own = gridContainer.own && uniqueSelector(gridContainer.own);
    const tracks = gridFitTracks(gridContainer.node);
    if (up && own) return { type: 'gridcollapse', label, target: own, up, tracks };
    if (up) return { type: 'gridcollapse', label, target: up, tracks };
    if (own) return { type: 'gridcollapse', label, target: own, tracks: null };
    return null;
  }
  function contentSized(el) {
    const value = el.style.getPropertyValue('width');
    const priority = el.style.getPropertyPriority('width');
    el.style.setProperty('width', 'auto', 'important');
    const wide = el.getBoundingClientRect().right - vw > 1;
    if (value) el.style.setProperty('width', value, priority);
    else el.style.removeProperty('width');
    return wide;
  }
  function classify(el, rect) {
    const style = getComputedStyle(el);
    const label = selectorFor(el);
    if (style.position === 'absolute' && rect.left >= vw - 1) {
      const target = uniqueSelector(el);
      if (!target) return null;
      let clip = null;
      for (let node = el.parentElement; node && node !== document.body; node = node.parentElement) {
        if (!/^(HEADER|NAV)$/.test(node.tagName) || !spansViewport(node)) continue;
        clip = uniqueSelector(node);
        break;
      }
      return { type: 'offcanvas', label, target, clip };
    }
    if (Math.abs(translateXOf(style)) > 0.5 && parseFloat(style.opacity) < 1) {
      for (let node = el.parentElement; node && node !== document.body; node = node.parentElement) {
        if (!spansViewport(node)) continue;
        const target = uniqueSelector(node);
        return target ? { type: 'reveal', label, target } : null;
      }
      return null;
    }
    const needLeft = Math.max(0, -(parseFloat(style.marginLeft) || 0));
    const needRight = Math.max(0, -(parseFloat(style.marginRight) || 0));
    if (needLeft > 0.5 || needRight > 0.5) {
      const parent = el.parentElement;
      if (!parent) return null;
      const ps = getComputedStyle(parent);
      const padLeft = parseFloat(ps.paddingLeft) || 0;
      const padRight = parseFloat(ps.paddingRight) || 0;
      if (padLeft >= needLeft - 0.5 && padRight >= needRight - 0.5) return null;
      const target = uniqueSelector(parent);
      if (!target) return null;
      return { type: 'gutter', label, target, left: Math.max(padLeft, needLeft), right: Math.max(padRight, needRight) };
    }
    const flexContainer = flexNowrapContainer(el);
    const innerFlex = flexNowrapInside(el);
    const target = flexContainer && uniqueSelector(flexContainer);
    const inner = innerFlex && innerFlex !== flexContainer && uniqueSelector(innerFlex);
    if (target || inner) return { type: 'navwrap', label, target: target || inner, inner: target && inner ? inner : null };
    const gridCandidate = gridCollapseCandidate(el, label);
    if (gridCandidate) return gridCandidate;
    const scaled = style.transform.match(/^matrix\\(([^)]+)\\)$/);
    if (scaled) {
      const [a, b, c, , tx] = scaled[1].split(',').map(s => parseFloat(s.trim()));
      if (Math.abs(b) < 0.001 && Math.abs(c) < 0.001 && a > 1.001 && rect.right - vw > 1) {
        const originX = parseFloat(style.transformOrigin) || 0;
        const flatLeft = rect.left - originX * (1 - a) - tx;
        const flatRight = flatLeft + rect.width / a;
        const target = uniqueSelector(el);
        if (target && flatLeft >= -1 && flatRight <= vw + 1) return { type: 'unscale', label, target };
      }
    }
    const positional = rect.right - vw > 1 && flexContainer && positionalSelector(flexContainer);
    if (positional) return { type: 'navwrap', label, target: positional, inner: null };
    const tableTarget = el.tagName === 'TABLE' && rect.right - vw > 1 && uniqueSelector(el);
    if (tableTarget) return { type: 'table', label, target: tableTarget };
    return shrinkCandidate(el, rect, style, label);
  }
  function shrinkCandidate(el, rect, style, label) {
    if (rect.right - vw <= 1 || /^(absolute|fixed|sticky)$/.test(style.position)) return null;
    if (el.tagName === 'IMG' || el.tagName === 'VIDEO') return null;
    if (!contentSized(el)) return null;
    const target = uniqueSelector(el) || positionalSelector(el);
    return target ? { type: 'shrink', label, target } : null;
  }
  function wideMedia(el) {
    const found = [];
    for (const node of [el, ...el.querySelectorAll('img, video')]) {
      if (node.tagName !== 'IMG' && node.tagName !== 'VIDEO') continue;
      const r = node.getBoundingClientRect();
      if (r.right <= vw + 1) continue;
      const s = getComputedStyle(node);
      if (s.maxWidth.endsWith('%') || s.position === 'absolute' || s.position === 'fixed') continue;
      if (isClipped(node) || inFixed(node)) continue;
      const target = uniqueSelector(node);
      if (!target) continue;
      const heightAttr = parseFloat(node.getAttribute('height'));
      const natW = node.naturalWidth || node.videoWidth;
      const natH = node.naturalHeight || node.videoHeight;
      const keepsRatio = natW > 0 && natH > 0 && Math.abs(r.width * natH / (r.height * natW) - 1) < 0.02;
      const autoHeight = (heightAttr > 0 && Math.abs(heightAttr - r.height) < 1) || keepsRatio;
      if (!autoHeight && natW > 0 && s.objectFit === 'fill') continue;
      found.push({
        type: 'media',
        label: selectorFor(el),
        target,
        cap: s.maxWidth === 'none' ? null : s.maxWidth,
        autoHeight,
      });
    }
    return found;
  }
  function flexTextOverflow(el) {
    const s = getComputedStyle(el);
    if (s.display !== 'flex' && s.display !== 'inline-flex') return null;
    if (!/^row/.test(s.flexDirection) || s.overflowX !== 'visible') return null;
    if (s.position === 'fixed' || s.position === 'absolute' || s.position === 'sticky') return null;
    const range = document.createRange();
    const passes = [...el.childNodes].some(n => {
      if (n.nodeType !== 3 || !n.textContent.trim()) return false;
      range.selectNodeContents(n);
      return Array.from(range.getClientRects()).some(r => r.width > 0 && r.right > vw + 1);
    });
    return passes ? { nowrap: s.flexWrap === 'nowrap' } : null;
  }
  function flexTextCandidate(el, overflow) {
    if (!overflow.nowrap) return null;
    let target = uniqueSelector(el);
    if (!target && el.parentElement) {
      const parent = uniqueSelector(el.parentElement);
      const group = parent && `${parent} > ${selectorFor(el)}`;
      if (group && Array.from(document.querySelectorAll(group)).every(n => (flexTextOverflow(n) || {}).nowrap)) target = group;
    }
    return target ? { type: 'navwrap', label: `${selectorFor(el)}::text`, target, inner: null } : null;
  }
  function textPastEdge(el) {
    const range = document.createRange();
    return [...el.childNodes].some(n => {
      if (n.nodeType !== 3 || !n.textContent.trim()) return false;
      range.selectNodeContents(n);
      return Array.from(range.getClientRects()).some(r => r.width > 0 && r.right > vw + 1);
    });
  }
  function walk(el) {
    const reachable = el !== document.body && !isClipped(el) && !inFixed(el);
    const textOverflow = reachable ? flexTextOverflow(el) : null;
    const shrinkText = reachable && !textOverflow && getComputedStyle(el).overflowX === 'visible' && textPastEdge(el);
    if (textOverflow || shrinkText) result.offenders.push(`${selectorFor(el)}::text`);
    for (const child of el.children) {
      const rect = child.getBoundingClientRect();
      if (rect.right > vw) {
        if (isClipped(child) || inFixed(child)) continue;
        const candidate = classify(child, rect);
        const media = wideMedia(child);
        if (rect.right - vw <= 1 && !candidate && !media.length) {
          walk(child);
          continue;
        }
        result.offenders.push(selectorFor(child));
        const last = candidate && (candidate.type === 'table' || candidate.type === 'shrink');
        if (candidate && !last) result.candidates.push(candidate);
        result.candidates.push(...media);
        if (last) result.candidates.push(candidate);
        const fallback = candidate && /^(navwrap|gridcollapse)$/.test(candidate.type) ? shrinkCandidate(child, rect, getComputedStyle(child), candidate.label) : null;
        if (fallback) result.candidates.push(fallback);
        continue;
      }
      walk(child);
    }
    if (textOverflow) {
      const candidate = flexTextCandidate(el, textOverflow);
      if (candidate && !result.candidates.some(c => c.type === 'navwrap' && (c.target === candidate.target || c.inner === candidate.target))) result.candidates.push(candidate);
      const gridCandidate = gridCollapseCandidate(el, `${selectorFor(el)}::text`);
      if (gridCandidate) result.candidates.push(gridCandidate);
    }
    if (shrinkText) {
      const target = uniqueSelector(el) || positionalSelector(el);
      if (target) result.candidates.push({ type: 'shrink', label: `${selectorFor(el)}::text`, target });
    }
  }
  walk(document.body);
  return result;
}""".replace("__SELECTOR_HELPERS__", _SELECTOR_HELPERS)

_SCROLLERS_SCRIPT = """() => {
__SELECTOR_HELPERS__
  const result = [];
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if ((s.overflowX !== 'auto' && s.overflowX !== 'scroll') || el.clientWidth <= 0 || el.scrollWidth <= el.clientWidth + 1) continue;
    const selector = uniqueSelector(el) || positionalSelector(el);
    const animated = s.animationName !== 'none';
    const masked = (s.maskImage || 'none') !== 'none' || (s.webkitMaskImage || 'none') !== 'none';
    result.push({ selector, skip: animated || masked || s.direction === 'rtl' });
  }
  return result;
}""".replace("__SELECTOR_HELPERS__", _SELECTOR_HELPERS)

_CUE_PREAMBLE = (
    "@property --wra-cue-l { syntax: '<length>'; inherits: false; initial-value: 0px; }\n"
    "@property --wra-cue-r { syntax: '<length>'; inherits: false; initial-value: 0px; }\n"
    "@keyframes wra-scroll-cue { 0% { --wra-cue-l: 0px; --wra-cue-r: 24px; } 8% { --wra-cue-l: 24px; --wra-cue-r: 24px; } 92% { --wra-cue-l: 24px; --wra-cue-r: 24px; } 100% { --wra-cue-l: 24px; --wra-cue-r: 0px; } }"
)
_CUE_MASK = "linear-gradient(to right, transparent, #000 var(--wra-cue-l), #000 calc(100% - var(--wra-cue-r)), transparent)"

# Horizontal geometry of top-level blocks (body's and main's children) plus
# the full rects of the given selectors, to confirm a repair moved nothing
# it did not target.
_GEOMETRY_SCRIPT = """([selectors, held]) => {
  const nodes = Array.from(document.body.children);
  const main = document.querySelector('main');
  if (main) nodes.push(...main.children);
  const skip = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'TEMPLATE', 'NOSCRIPT']);
  const tops = nodes.filter(n => !skip.has(n.tagName)).map(n => {
    const r = n.getBoundingClientRect();
    return [r.left, r.width];
  });
  const rects = {};
  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    rects[selector] = [r.left, r.top, r.width, r.height];
  }
  const fits = {};
  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (el) fits[selector] = el.scrollWidth <= el.clientWidth + 1;
  }
  const range = document.createRange();
  const within = (node, edge) => Array.from(node.childNodes).every(n => {
    if (n.nodeType === 3) {
      if (!n.textContent.trim()) return true;
      range.selectNodeContents(n);
      return Array.from(range.getClientRects()).every(r => r.width === 0 || r.right <= edge + 1);
    }
    if (n.nodeType !== 1) return true;
    const s = getComputedStyle(n);
    if (s.display === 'none' || s.overflowX !== 'visible' || s.position === 'absolute' || s.position === 'fixed') return true;
    return within(n, edge);
  });
  const holds = {};
  for (const selector of held) {
    holds[selector] = Array.from(document.querySelectorAll(selector)).every(el => {
      const s = getComputedStyle(el);
      return within(el, el.getBoundingClientRect().right - (parseFloat(s.paddingRight) || 0) - (parseFloat(s.borderRightWidth) || 0));
    });
  }
  return { tops, rects, fits, holds };
}"""


def _measure(page, html: str, selectors: list[str], load, held: list[str] = (), scrollers: bool = False) -> dict[int, dict]:
    snapshots: dict[int, dict] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        snapshots[width] = {**page.evaluate(_DETECT_SCRIPT), **page.evaluate(_GEOMETRY_SCRIPT, [selectors, list(held)])}
        if scrollers:
            snapshots[width]["scrollers"] = page.evaluate(_SCROLLERS_SCRIPT)
    return snapshots


def _revealed_scrollers(page, html: str, load, baseline: dict[int, dict]) -> dict[str, list[int]]:
    revealed: dict[str, list[int]] = {}
    for width in _VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 800})
        load(page, html)
        before = {found["selector"] for found in baseline[width]["scrollers"]}
        for found in page.evaluate(_SCROLLERS_SCRIPT):
            if found["selector"] and found["selector"] not in before and not found["skip"]:
                revealed.setdefault(found["selector"], []).append(width)
    return revealed


def _cue(selector: str, widths: list[int]) -> str:
    rule = f"@supports (animation-timeline: scroll()) {{ {selector} {{ -webkit-mask-image: {_CUE_MASK} !important; mask-image: {_CUE_MASK} !important; -webkit-mask-clip: no-clip !important; mask-clip: no-clip !important; animation: wra-scroll-cue linear both !important; animation-timeline: scroll(self inline) !important; }} }}"
    if all(width in widths for width in _VIEWPORTS):
        return rule
    bands: list[list[int]] = []
    for index, width in enumerate(_VIEWPORTS):
        if width not in widths:
            continue
        if bands and bands[-1][1] == index - 1:
            bands[-1][1] = index
        else:
            bands.append([index, index])
    blocks = []
    for first, last in bands:
        conditions = [f"(min-width: {_VIEWPORTS[first - 1] + 1}px)"] if first else []
        if last + 1 < len(_VIEWPORTS):
            conditions.append(f"(max-width: {_VIEWPORTS[last + 1] - 1}px)")
        blocks.append(f"@media {' and '.join(conditions)} {{ {rule} }}")
    return "\n".join(blocks)


def _collect(snapshots: dict[int, dict]) -> list[dict]:
    candidates: dict[tuple[str, str], dict] = {}
    for width, snapshot in snapshots.items():
        for found in snapshot["candidates"]:
            candidate = candidates.setdefault((found["type"], found["target"]), {**found, "widths": []})
            candidate["widths"].append(width)
            if found["type"] == "gutter":
                candidate["left"] = max(candidate["left"], found["left"])
                candidate["right"] = max(candidate["right"], found["right"])
            elif found["type"] == "offcanvas" and not candidate.get("clip"):
                candidate["clip"] = found.get("clip")
            elif found["type"] == "navwrap" and not candidate.get("inner"):
                candidate["inner"] = found.get("inner")
            elif found["type"] == "gridcollapse" and candidate.get("tracks") and len(found.get("tracks") or []) != len(candidate["tracks"]):
                candidate["tracks"] = None
    return list(candidates.values())


def _collapse(selector: str) -> str:
    return f"{selector} {{ grid-template-columns: minmax(0,1fr) !important; }}"


def _options(candidate: dict) -> list[tuple[str, str | None, bool, str | None]]:
    target = candidate["target"]
    if candidate["type"] == "gutter":
        left, right = round(candidate["left"], 2), round(candidate["right"], 2)
        return [(f"{target} {{ padding-left: {left:g}px !important; padding-right: {right:g}px !important; }}", target, False, None)]
    if candidate["type"] == "offcanvas":
        options = [(f"{target} {{ position: fixed !important; }}", target, True, None)]
        if candidate.get("clip"):
            options.append((f"{candidate['clip']} {{ overflow-x: clip !important; }}", candidate["clip"], False, None))
        return options
    if candidate["type"] == "navwrap":
        rules = [f"{selector} {{ flex-wrap: wrap !important; max-width: 100% !important; }}" for selector in (candidate.get("inner"), target) if selector]
        combined = [" ".join(rules)] if len(rules) > 1 else []
        return [(css, None, False, None) for css in [*rules, *combined]]
    if candidate["type"] == "gridcollapse":
        up = candidate.get("up")
        options = [(_collapse(target), None, False, None)]
        if up:
            options.append((_collapse(up), None, False, None))
        grid = up or target
        if candidate.get("tracks"):
            columns = " ".join(f"minmax(0, {track:g}fr)" for track in candidate["tracks"])
            proportional = f"{grid} {{ grid-template-columns: {columns} !important; }}"
            lowest = min(candidate["widths"])
            if lowest > min(_VIEWPORTS):
                proportional = f"@media (min-width: {lowest}px) {{ {proportional} }} @media (max-width: {lowest - 1}px) {{ {_collapse(grid)} }}"
            options.insert(1 if up else 0, (proportional, None, False, grid))
        if up:
            options.append((f"{_collapse(target)} {_collapse(up)}", None, False, None))
        return options
    if candidate["type"] == "unscale":
        return [(f"{target} {{ transform: none !important; }}", None, False, None)]
    if candidate["type"] == "media":
        cap = f"min({candidate['cap']}, 100%)" if candidate.get("cap") else "100%"
        height = " height: auto !important;" if candidate.get("autoHeight") else ""
        return [(f"{target} {{ max-width: {cap} !important;{height} }}", target, True, None)]
    if candidate["type"] == "table":
        return [(f"{target} {{ display: block !important; max-width: 100% !important; overflow-x: auto !important; }}", None, False, None)]
    if candidate["type"] == "shrink":
        return [
            (f"{target} {{ min-width: 0 !important; max-width: 100% !important; }}", None, False, None),
            (f"{target} {{ min-width: 0 !important; max-width: 100% !important; overflow-wrap: anywhere !important; }}", None, False, None),
        ]
    return [(f"{target} {{ overflow-x: clip !important; }}", target, False, None)]


def _scoped(css: str, widths: list[int]) -> str:
    larger = [width for width in _VIEWPORTS if width > max(widths)]
    if not larger:
        return css
    return f"@media (max-width: {min(larger) - 1}px) {{ {css} }}"


def _same(before: list[float], after: list[float]) -> bool:
    return len(before) == len(after) and all(abs(b - a) <= 1 for b, a in zip(before, after))


def _top_kept(before: list[float], after: list[float], viewport: int, narrowing: bool) -> bool:
    if _same(before, after):
        return True
    return narrowing and before[0] + before[1] > viewport + 1 and abs(before[0] - after[0]) <= 1 and after[0] + after[1] <= viewport + 1


def _exposes_nothing(reference: dict[int, dict], trial: dict[int, dict], widths: list[int]) -> bool:
    return all(not Counter(trial[width]["offenders"]) - Counter(reference[width]["offenders"]) for width in widths)


def _verified(baseline: dict[int, dict], trial: dict[int, dict], candidate: dict, checks: list[tuple[str | None, bool, list[int]]], fits: list[str] = (), held: list[str] = ()) -> bool:
    for width in _VIEWPORTS:
        before, after = baseline[width], trial[width]
        if width in candidate["widths"] and not all(after["fits"].get(fit) for fit in fits):
            return False
        if width <= max(candidate["widths"]) and not all(after["holds"].get(selector) for selector in held):
            return False
        if after["scrollWidth"] > max(before["scrollWidth"], after["viewport"]):
            return False
        if candidate["type"] == "unscale" and width in candidate["widths"] and after["scrollWidth"] >= before["scrollWidth"]:
            return False
        if len(before["tops"]) != len(after["tops"]) or not all(_top_kept(b, a, after["viewport"], candidate["type"] == "shrink") for b, a in zip(before["tops"], after["tops"])):
            return False
        for check, full_rect, exempt in checks:
            if width in exempt:
                continue
            rect_before, rect_after = before["rects"].get(check), after["rects"].get(check)
            if (rect_before is None) != (rect_after is None):
                return False
            if rect_before is not None:
                keep = [0, 2, 3] if candidate["type"] == "media" else [0, 1, 2, 3] if full_rect else [0, 2]
                if not _same([rect_before[i] for i in keep], [rect_after[i] for i in keep]):
                    return False
        if width in candidate["widths"] and candidate["label"] in after["offenders"]:
            return False
    return True


def _repair_members(page, html: str, load, checks: list[str], baseline: dict[int, dict], members: list[dict], accepted: list[str], repairs: list[str]) -> bool:
    kind, label = members[0]["type"], members[0]["label"]
    widths = sorted({width for member in members for width in member["widths"]})
    member_options = [
        [(_scoped(css, member["widths"]), check, full_rect, member["widths"] if member["type"] == "media" else [], fit) for css, check, full_rect, fit in _options(member)]
        for member in members
    ]
    current = None
    if accepted:
        current = _measure(page, _inject_style(html, "\n".join(accepted), "overflow"), checks, load)
        if all(label not in current[width]["offenders"] for width in widths):
            return True
    for index in range(max(len(options) for options in member_options)):
        parts = [options[index] for options in member_options if index < len(options)]
        css = "\n".join(part[0] for part in parts)
        fits = [part[4] for part in parts if part[4]]
        held = [member["target"] for member in members] if kind == "shrink" and index == 0 else []
        trial = _measure(page, _inject_style(html, "\n".join([*accepted, css]), "overflow"), sorted({*checks, *fits}), load, held)
        if _verified(baseline, trial, {"type": kind, "label": label, "widths": widths}, [(part[1], part[2], part[3]) for part in parts], fits, held) and (kind != "shrink" or _exposes_nothing(current or baseline, trial, widths)):
            accepted.append(css)
            repairs.extend(f"{kind}: {label} @{width}px" for width in widths)
            return True
    return False


def repair_horizontal_overflow(html: str, asset_root: str | Path | None = None) -> tuple[str, list[str]]:
    """Fix known causes of horizontal page overflow with targeted CSS.

    Renders the page at each viewport width, classifies elements that push
    the page wider than the viewport (uncompensated negative-margin gutters,
    absolutely positioned off-canvas menus parked past the right edge,
    unrevealed scroll-reveal elements with a horizontal offset, in-flow flex
    rows that refuse to wrap, multi-column grids that don't fit,
    elements scaled up past the right edge by their own transform, tables
    wider than the viewport, images or videos whose own box passes the right edge, and
    boxes sized past the edge by unbreakable text or a nowrap scroller inside them), and
    tries a per-element CSS fix for each. A fix is kept only if re-rendering
    shows the element no longer overflows, no viewport gets wider, and
    top-level blocks and the fix's own target keep their horizontal
    position and width. An element the fixes turn into a horizontal scroller
    gets an edge-fade scroll cue so the clipped content reads as scrollable. Unclassified
    overflow is left alone. Returns the input unchanged if nothing verified or
    Playwright is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is unavailable, skipping overflow repair")
        return html, []

    accepted: list[str] = []
    repairs: list[str] = []
    try:
        with sync_playwright() as playwright, page_loader(asset_root) as load:
            browser = launch_guarded(playwright)
            try:
                page = new_guarded_page(browser, asset_root)
                snapshots = _measure(page, html, [], load)
                candidates = _collect(snapshots)
                if not candidates:
                    overflowing = [f"{width}px ({', '.join(snapshot['offenders'])})" for width, snapshot in snapshots.items() if snapshot["scrollWidth"] > snapshot["viewport"]]
                    if overflowing:
                        logger.warning("Horizontal overflow has no repair candidate: %s", "; ".join(overflowing))
                    return html, []
                checks = sorted({check for candidate in candidates for _, check, _, _ in _options(candidate) if check is not None})
                baseline = _measure(page, html, checks, load, scrollers=True)
                groups: dict[tuple[str, str], list[dict]] = {}
                for candidate in candidates:
                    groups.setdefault((candidate["type"], candidate["label"]), []).append(candidate)
                ordered = sorted(groups.items(), key=lambda item: item[0][0] == "shrink")
                for _, members in ordered:
                    if _repair_members(page, html, load, checks, baseline, members, accepted, repairs) or len(members) == 1:
                        continue
                    for member in members:
                        _repair_members(page, html, load, checks, baseline, [member], accepted, repairs)
                if accepted:
                    revealed = _revealed_scrollers(page, _inject_style(html, "\n".join(accepted), "overflow"), load, baseline)
                    if revealed:
                        accepted.extend([_CUE_PREAMBLE, *(_cue(selector, widths) for selector, widths in revealed.items())])
                        repairs.extend(f"scrollcue: {selector} @{width}px" for selector, widths in revealed.items() for width in widths)
            finally:
                browser.close()
    except Exception:
        logger.warning("Playwright launch failed, skipping overflow repair", exc_info=True)
        return html, []

    if not accepted:
        logger.warning("Overflow repair found candidates but none verified, leaving HTML as-is")
        return html, []
    return _inject_style(html, "\n".join(accepted), "overflow"), repairs
