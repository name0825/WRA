"""WCAG text-contrast measurement for rendered pages.

The browser script collects text elements whose background is a plain colour
stack. Anything the ancestor walk cannot see through (background images,
gradients, filters, blend modes, translucent ancestors, painted pseudo-elements,
or any other painted element overlapping the text) makes the background unknown
and the element is skipped: a missed failure is preferred to a false one.
"""

import re

from wra.browser.models import ContrastFailure

NORMAL_TEXT_MIN_RATIO = 4.5
LARGE_TEXT_MIN_RATIO = 3.0
MAX_CONTRAST_FAILURES = 20
_WHITE = (255.0, 255.0, 255.0)
_RGB = re.compile(r"^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$")

RGB_HELPER_JS = """  const rgbCache = new Map();
  let probe = null;
  const rgb = (value) => {
    if (!value || value.startsWith('rgb')) return value;
    if (rgbCache.has(value)) return rgbCache.get(value);
    let result = value;
    try {
      if (CSS.supports('color', value)) {
        if (!probe) {
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 1;
          probe = canvas.getContext('2d', {willReadFrequently: true});
        }
        let kept = 0;
        const over = (base) => {
          probe.fillStyle = base;
          probe.fillRect(0, 0, 1, 1);
          const before = probe.fillStyle;
          probe.fillStyle = value;
          if (probe.fillStyle === before) kept++;
          probe.fillRect(0, 0, 1, 1);
          return probe.getImageData(0, 0, 1, 1).data;
        };
        const white = over('#ffffff'), black = over('#000000');
        const alpha = 1 - (white[0] + white[1] + white[2] - black[0] - black[1] - black[2]) / 765;
        if (kept === 2) result = value;
        else if (alpha < 0.002) result = 'rgba(0, 0, 0, 0)';
        else if (alpha > 0.998) result = `rgb(${black[0]}, ${black[1]}, ${black[2]})`;
        else result = `rgba(${[0, 1, 2].map((i) => Math.min(255, Math.round(black[i] / alpha))).join(', ')}, ${alpha.toFixed(3)})`;
      }
    } catch (e) {}
    rgbCache.set(value, result);
    return result;
  };"""

CONTRAST_CANDIDATES_SCRIPT = """() => {
  const TRANSPARENT = /^rgba\\([^)]*,\\s*0\\)$/;
""" + RGB_HELPER_JS + """
  const skipTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'TITLE']);
  const mediaTags = new Set(['IMG', 'VIDEO', 'CANVAS', 'svg', 'PICTURE', 'IFRAME', 'OBJECT', 'EMBED']);
  const docWidth = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0);
  const docHeight = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  const all = Array.from(document.querySelectorAll('body *'));
  const painted = [];
  for (const el of all) {
    const style = getComputedStyle(el);
    if (mediaTags.has(el.tagName) || style.backgroundImage !== 'none' || !TRANSPARENT.test(rgb(style.backgroundColor))) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) painted.push([el, rect]);
    }
  }
  const pseudoCache = new Map();
  const pseudoPainted = (node) => {
    if (!pseudoCache.has(node)) {
      let found = false;
      for (const pseudo of ['::before', '::after']) {
        const s = getComputedStyle(node, pseudo);
        if (s.content !== 'none' && s.content !== 'normal' && (s.backgroundImage !== 'none' || !TRANSPARENT.test(rgb(s.backgroundColor)))) found = true;
      }
      pseudoCache.set(node, found);
    }
    return pseudoCache.get(node);
  };
  const seen = new Set();
  const out = [];
  for (const el of all) {
    if (out.length >= 1000) break;
    if (skipTags.has(el.tagName) || el.closest('svg')) continue;
    let text = '';
    for (const node of el.childNodes) if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
    text = text.replace(/\\s+/g, ' ').trim();
    if (!text) continue;
    const style = getComputedStyle(el);
    if (style.visibility !== 'visible' || parseFloat(style.opacity) === 0) continue;
    if (el.checkVisibility && !el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const left = rect.left + scrollX, top = rect.top + scrollY;
    if (left + rect.width <= 0 || top + rect.height <= 0 || left >= docWidth || top >= docHeight) continue;
    if (style.textShadow !== 'none' || style.webkitBackgroundClip === 'text' || style.backgroundClip === 'text') continue;
    const layers = [];
    let unknown = false;
    for (let node = el; node; node = node.parentElement) {
      const s = node === el ? style : getComputedStyle(node);
      if (s.backgroundImage !== 'none' || s.filter !== 'none' || s.backdropFilter !== 'none' || s.mixBlendMode !== 'normal' || parseFloat(s.opacity) < 1 || pseudoPainted(node)) { unknown = true; break; }
      const background = rgb(s.backgroundColor);
      if (!TRANSPARENT.test(background)) {
        layers.push(background);
        if (background.startsWith('rgb(')) break;
      }
    }
    if (unknown) continue;
    for (const [other, otherRect] of painted) {
      if (other === el || other.contains(el) || el.contains(other)) continue;
      if (otherRect.left < rect.right && otherRect.right > rect.left && otherRect.top < rect.bottom && otherRect.bottom > rect.top) { unknown = true; break; }
    }
    if (unknown) continue;
    const color = rgb(style.webkitTextFillColor || style.color);
    const snippet = text.slice(0, 60);
    const idx = el.getAttribute('data-wra-i');
    const key = color + '|' + layers.join('|') + '|' + snippet + '|' + (idx || '');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({text: snippet, color, layers, size: parseFloat(style.fontSize) || 0, weight: parseInt(style.fontWeight, 10) || 400, idx});
  }
  return out;
}"""


def parse_css_color(value: str) -> tuple[float, float, float, float] | None:
    """Parse a computed `rgb()`/`rgba()` colour; any other syntax is unknown (None)."""
    match = _RGB.match(value.strip())
    if match is None:
        return None
    red, green, blue = (float(match.group(index)) for index in (1, 2, 3))
    alpha_text = match.group(4)
    if alpha_text is None:
        alpha = 1.0
    elif alpha_text.endswith("%"):
        alpha = float(alpha_text[:-1]) / 100
    else:
        alpha = float(alpha_text)
    return red, green, blue, min(max(alpha, 0.0), 1.0)


def _composite(color: tuple[float, float, float, float], base: tuple[float, float, float]) -> tuple[float, float, float]:
    alpha = color[3]
    return tuple(top * alpha + bottom * (1 - alpha) for top, bottom in zip(color[:3], base))


def effective_background(layers: list[str]) -> tuple[float, float, float] | None:
    """Composite background layers (innermost first) over the white canvas."""
    parsed = [parse_css_color(layer) for layer in layers]
    if any(color is None for color in parsed):
        return None
    base = _WHITE
    for color in reversed(parsed):
        base = _composite(color, base)
    return base


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: tuple[float, float, float], background: tuple[float, float, float]) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def is_large_text(font_size: float, font_weight: float) -> bool:
    return font_size >= 24 or (font_size >= 18.66 and font_weight >= 700)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(value):02x}" for value in rgb)


def contrast_failures(candidates: list[dict]) -> list[ContrastFailure]:
    """Texts below the WCAG AA minimum (4.5:1, or 3:1 for large text), worst first."""
    failures: dict[tuple[str, str, str], ContrastFailure] = {}
    for candidate in candidates:
        try:
            text = str(candidate["text"])[:60]
            color = parse_css_color(str(candidate["color"]))
            background = effective_background([str(layer) for layer in candidate["layers"]])
            size = float(candidate.get("size") or 0)
            weight = float(candidate.get("weight") or 400)
        except (KeyError, TypeError, ValueError):
            continue
        if not any(char.isalnum() for char in text) or color is None or background is None:
            continue
        foreground = _composite(color, background)
        ratio = contrast_ratio(foreground, background)
        minimum = LARGE_TEXT_MIN_RATIO if is_large_text(size, weight) else NORMAL_TEXT_MIN_RATIO
        if ratio >= minimum:
            continue
        failure = ContrastFailure(text=text, ratio=round(ratio, 2), foreground=_hex(foreground), background=_hex(background))
        failures.setdefault((failure.foreground, failure.background, failure.text), failure)
    return sorted(failures.values(), key=lambda failure: failure.ratio)[:MAX_CONTRAST_FAILURES]
