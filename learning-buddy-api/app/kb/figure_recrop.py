"""Upload Agent-owned PDF crops onto the existing MinerU MinIO keys.

裁切算法在 textbook-agent 的 `workspace/tools/figure_recrop.py`（agent 可改）。
这里只读 PDF、调 `iter_figure_jpegs`、把 JPEG 写回
`kb/pdfs/{id}/assets/mineru/{name}`，Markdown URL 不变。

本模块是知识库导入管道，不 import 35B 裁切路径。归属见
doc/fig-code-ownership.md。
"""
from __future__ import annotations

import logging
from typing import Any

from app.kb.assets import content_type_for_name, upload_bytes
from app.kb.workspace_figures import figure_recrop as load_algo
from app.kb.workspace_store import mineru_dir, pdf_path

logger = logging.getLogger(__name__)

FIGURE_CROP_DPI = 300
FIGURE_JPEG_QUALITY = 95


def _algo() -> Any:
    return load_algo()


def _load_blocks(book_id: str) -> list[dict[str, Any]]:
    import json

    folder = mineru_dir(book_id)
    path = folder / "blocks.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("blocks") or data.get("content_list") or []
    if not isinstance(data, list):
        return []
    return [b for b in data if isinstance(b, dict)]


def recrop_figures(
    book_id: str,
    pdf_bytes: bytes,
    blocks: list[dict[str, Any]] | None = None,
    *,
    dpi: int | None = None,
    quality: int | None = None,
) -> int:
    """Overwrite MinerU figure assets with sharper PDF crops. Returns count."""
    sid = (book_id or "").strip()
    if not sid or not pdf_bytes:
        return 0
    algo = _algo()
    if algo is None:
        logger.warning("figure recrop skipped book=%s: workspace tools missing", sid)
        return 0
    items = [b for b in (blocks or []) if isinstance(b, dict)]
    if not items:
        items = _load_blocks(sid)
    if not items:
        return 0
    crop_dpi = int(algo.FIGURE_CROP_DPI if dpi is None else dpi)
    crop_quality = int(algo.FIGURE_JPEG_QUALITY if quality is None else quality)
    done = 0
    for name, data in algo.iter_figure_jpegs(
        pdf_bytes, items, dpi=crop_dpi, quality=crop_quality
    ):
        key = f"kb/pdfs/{sid}/assets/mineru/{name}"
        try:
            upload_bytes(key, data, content_type=content_type_for_name(name))
            done += 1
        except Exception as exc:
            logger.warning("figure recrop upload failed book=%s name=%s: %s", sid, name[:16], exc)
    if done:
        logger.info("figure recrop book=%s count=%s dpi=%s q=%s", sid, done, crop_dpi, crop_quality)
    return done


def recrop_book(book_id: str, **kwargs: Any) -> int:
    path = pdf_path(book_id)
    if not path.is_file():
        return 0
    raw = path.read_bytes()
    if not raw:
        return 0
    return recrop_figures(book_id, raw, **kwargs)


def refresh_page_jpegs(book_id: str) -> int:
    """Re-render workspace page previews at the current page JPEG setting."""
    from app.kb.pdf_pages import open_pdf
    from app.kb.workspace_store import image_path

    path = pdf_path(book_id)
    if not path.is_file():
        return 0
    raw = path.read_bytes()
    if not raw:
        return 0
    n = 0
    with open_pdf(raw) as pages:
        total = pages.page_count()
        for page_no in range(1, total + 1):
            dest = image_path(book_id, page_no)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(pages.page_jpeg(page_no))
            n += 1
    return n
