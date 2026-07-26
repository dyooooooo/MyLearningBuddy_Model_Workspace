"""PDF 下载与页渲染（三角裁决与 preview 共用）。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import SERVICE_ROOT, Settings

_MAX_PAGES = 12


def parse_pages(spec: str, page_count: int, *, max_pages: int = _MAX_PAGES) -> list[int]:
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return list(range(1, page_count + 1))[:max_pages]
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    uniq = sorted({p for p in out if 1 <= p <= page_count})
    return uniq[:max_pages]


def preview_dir(minio_key: str) -> Path:
    h = hashlib.sha1(minio_key.encode()).hexdigest()[:12]
    d = SERVICE_ROOT / "data" / "preview" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_pdf(settings: Settings, minio_key: str) -> bytes:
    if not (minio_key.startswith("corpus/imports/") or minio_key.startswith("import35b/")):
        raise ValueError("minio_key 仅支持 corpus/imports/ 或 import35b/ 前缀")
    url = f"{settings.gateway_base.rstrip('/')}/api/v1/studio/import/object"
    r = httpx.get(url, params={"key": minio_key}, timeout=120)
    r.raise_for_status()
    return r.content


def render_pages(
    pdf_bytes: bytes,
    minio_key: str,
    pages: list[int],
    *,
    dpi: int = 72,
) -> dict[str, Any]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = doc.page_count
        out_dir = preview_dir(minio_key)
        zoom = max(0.5, min(float(dpi), 110.0) / 72.0)
        images: dict[int, str] = {}
        jpeg_bytes: dict[int, bytes] = {}
        for pn in pages:
            if pn < 1 or pn > page_count:
                continue
            dest = out_dir / f"page-{pn:03d}.jpg"
            if not dest.exists():
                pix = doc[pn - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                pix.save(str(dest), jpg_quality=70)
            images[pn] = str(dest)
            jpeg_bytes[pn] = dest.read_bytes()
        return {
            "page_count": page_count,
            "images": images,
            "jpeg_bytes": jpeg_bytes,
        }
    finally:
        doc.close()


def pages_spec(pages: list[int]) -> str:
    if not pages:
        return ""
    return f"{pages[0]}-{pages[-1]}" if pages == list(range(pages[0], pages[-1] + 1)) else ",".join(
        str(p) for p in pages
    )


_PAGE_RANGE_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")


def validate_pages_expr(pages: str) -> bool:
    s = (pages or "").strip().lower()
    return s in ("", "all") or bool(_PAGE_RANGE_RE.match(s))
