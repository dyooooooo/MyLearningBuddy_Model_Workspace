"""Fast pre-OCR frame comparison — skip vLLM when pixels barely changed."""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Mean absolute difference (0–1) below this → treat as duplicate frame.
SKIP_OCR_DIFF_THRESHOLD = 0.012
_DIFF_THUMB_EDGE = 512


def _gray_thumbnail(image_bytes: bytes, max_edge: int = _DIFF_THUMB_EDGE) -> Image.Image:
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("L")
        w, h = im.size
        edge = max(w, h)
        if edge > max_edge:
            scale = max_edge / edge
            im = im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.BILINEAR,
            )
        return im.copy()


def mean_abs_diff_ratio(prev_bytes: bytes, curr_bytes: bytes) -> float:
    """Return mean per-pixel absolute diff in [0, 1] on downscaled grayscale."""
    a = _gray_thumbnail(prev_bytes)
    b = _gray_thumbnail(curr_bytes)
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    pa = a.tobytes()
    pb = b.tobytes()
    if len(pa) != len(pb) or not pa:
        return 1.0
    mad = sum(abs(x - y) for x, y in zip(pa, pb, strict=True)) / len(pa)
    return mad / 255.0


def should_skip_ocr(prev_bytes: bytes | None, curr_bytes: bytes) -> tuple[bool, float]:
    if not prev_bytes:
        return False, 1.0
    ratio = mean_abs_diff_ratio(prev_bytes, curr_bytes)
    skip = ratio < SKIP_OCR_DIFF_THRESHOLD
    if skip:
        logger.info("pre-OCR skip: image diff ratio=%.4f < %.4f", ratio, SKIP_OCR_DIFF_THRESHOLD)
    return skip, ratio
