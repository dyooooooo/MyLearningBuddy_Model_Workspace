"""Re-crop textbook figures from the original PDF at a higher raster setting.

单一实现，见 doc/fig-code-ownership.md。这里只产出 JPEG 字节，不碰 MinIO、
不 import `app.*`。buddy-api 的 `app/kb/figure_recrop.py` 负责上传。

改清晰度：调 ``FIGURE_CROP_DPI`` / ``FIGURE_JPEG_QUALITY`` /
``PAGE_JPEG_DPI`` / ``PAGE_JPEG_QUALITY``。
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FIGURE_CROP_DPI = 300
FIGURE_JPEG_QUALITY = 95
PAGE_JPEG_DPI = 150
PAGE_JPEG_QUALITY = 92
_PAD_PT = 1.5
_FIGURE_TYPES = frozenset({"image", "chart", "figure"})


def _equation_display() -> Any:
    name = "kb_workspace_equation_display"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "equation_display.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def is_diagram_latex(tex: str) -> bool:
    return bool(_equation_display().is_diagram_latex(tex))


def diagram_asset_name(page_no: int, bbox: tuple[float, float, float, float]) -> str:
    return str(_equation_display().diagram_asset_name(page_no, bbox))


def block_bbox(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    return _equation_display().block_bbox(block)


def block_page_no(block: dict[str, Any]) -> int:
    return int(_equation_display().block_page_no(block) or 0)


def page_jpeg_settings() -> tuple[int, int]:
    return PAGE_JPEG_DPI, PAGE_JPEG_QUALITY


def _xyxy(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    return _equation_display().block_bbox(block)


def _page_no(block: dict[str, Any]) -> int:
    return int(_equation_display().block_page_no(block) or 0)


def _asset_name(block: dict[str, Any]) -> str:
    candidates: list[Any] = [block.get(k) for k in ("img_path", "image_path", "src")]
    content = block.get("content")
    if isinstance(content, dict):
        candidates += [content.get(k) for k in ("img_path", "image_path", "src", "path")]
        source = content.get("image_source")
        if isinstance(source, dict):
            candidates += [source.get(k) for k in ("path", "img_path", "image_path", "src")]
        elif isinstance(source, str):
            candidates.append(source)
    for cand in candidates:
        if isinstance(cand, str) and cand.strip():
            return cand.strip().replace("\\", "/").rsplit("/", 1)[-1]
    return ""


def crop_one(
    pdf_bytes: bytes,
    page_no: int,
    bbox: tuple[float, float, float, float],
    *,
    dpi: int = FIGURE_CROP_DPI,
    quality: int = FIGURE_JPEG_QUALITY,
) -> bytes | None:
    """Rasterize one 0-1000 bbox on a 1-based page. Returns JPEG bytes or None."""
    import fitz

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.warning("figure recrop open failed: %s", exc)
        return None
    try:
        if page_no < 1 or page_no > doc.page_count:
            return None
        page = doc.load_page(page_no - 1)
        pw, ph = float(page.rect.width), float(page.rect.height)
        x0, y0, x1, y1 = bbox
        clip = fitz.Rect(
            x0 / 1000.0 * pw - _PAD_PT,
            y0 / 1000.0 * ph - _PAD_PT,
            x1 / 1000.0 * pw + _PAD_PT,
            y1 / 1000.0 * ph + _PAD_PT,
        ) & page.rect
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            return None
        pix = page.get_pixmap(dpi=dpi, alpha=False, clip=clip)
        return pix.tobytes("jpeg", jpg_quality=quality)
    except Exception as exc:
        logger.warning("figure recrop page=%s failed: %s", page_no, exc)
        return None
    finally:
        doc.close()


def iter_figure_jpegs(
    pdf_bytes: bytes,
    blocks: list[dict[str, Any]],
    *,
    dpi: int = FIGURE_CROP_DPI,
    quality: int = FIGURE_JPEG_QUALITY,
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(asset filename, jpeg bytes)`` for each unique figure block."""
    if not pdf_bytes:
        return
    eq = _equation_display()
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "").lower()
        xy = _xyxy(block)
        page_no = _page_no(block)
        if xy is None or page_no < 1:
            continue
        if kind in _FIGURE_TYPES:
            name = _asset_name(block)
        elif eq.is_diagram_block(block):
            name = _asset_name(block) or eq.diagram_asset_name(page_no, xy)
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        data = crop_one(pdf_bytes, page_no, xy, dpi=dpi, quality=quality)
        if data:
            yield name, data
