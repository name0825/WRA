import io
import logging
import math

logger = logging.getLogger(__name__)

PIXELS_PER_IMAGE_TOKEN = 1024
MIN_SCREENSHOT_PIXELS = 1_000_000


def fit_screenshots(paths: list[str], max_pixels: int | None) -> list[str | bytes]:
    if max_pixels is None:
        return paths
    from PIL import Image

    with_sizes = []
    for path in paths:
        with Image.open(path) as image:
            with_sizes.append((path, image.size))
    total = sum(width * height for _, (width, height) in with_sizes)
    if total <= max_pixels:
        return paths
    scale = math.sqrt(max_pixels / total)
    fitted: list[str | bytes] = []
    scaled_total = 0
    for path, (width, height) in with_sizes:
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        scaled_total += size[0] * size[1]
        with Image.open(path) as image:
            buffer = io.BytesIO()
            image.resize(size, Image.LANCZOS).save(buffer, format="PNG")
        fitted.append(buffer.getvalue())
    logger.info("Evaluation screenshots downscaled: pixels=%d->%d scale=%.3f", total, scaled_total, scale)
    return fitted


def screenshot_pixels(paths: list[str]) -> int:
    from PIL import Image

    total = 0
    for path in paths:
        with Image.open(path) as image:
            total += image.width * image.height
    return total
