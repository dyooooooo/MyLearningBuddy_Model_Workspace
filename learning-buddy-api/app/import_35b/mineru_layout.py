"""MinerU LayoutStage for the 35B import line (structure only → layout hints)."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.clients.minio_client import get_object_bytes, put_import_bytes
from app.import_35b.assets import assert_allowed_asset_key, upload_import_asset
from app.services.pdf_convert import convert_pdf_bytes

logger = logging.getLogger(__name__)

_CAPTION_KEYS = ("image_caption", "table_caption", "caption")


@dataclass
class LayoutIndex:
    """Per-page MinerU layout hints + book-level stats."""

    by_page: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    content_list: list[dict[str, Any]] = field(default_factory=list)


def _caption_lines(raw: object) -> str:
    if isinstance(raw, list):
        lines = [str(x).strip() for x in raw if str(x).strip()]
        return "\n".join(lines)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _normalize_bbox(raw: object) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        vals = [float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])]
    except (TypeError, ValueError):
        return None
    # Heuristic: coords already in 0–1000 page space → leave as-is;
    # larger pixel/PDF spaces → scale so max axis maps to 1000.
    mx = max(abs(v) for v in vals)
    if mx > 1000.0:
        scale = 1000.0 / mx
        vals = [round(v * scale, 2) for v in vals]
    return vals


def _map_hint_type(mineru_type: str, text: str) -> str:
    btype = mineru_type or "text"
    if btype in ("aside_text", "page_aside_text"):
        return "paragraph"
    if btype in ("image", "chart"):
        return "figure"
    if btype == "equation":
        return "equation"
    if btype == "table":
        return "table"
    if btype in ("title", "header"):
        return "title" if text.strip().startswith("第") else "heading"
    if text.strip().startswith(("- ", "* ", "1.", "（1）", "(1)")):
        return "list"
    return "paragraph" if text.strip() else "paragraph"


def _block_to_hint(raw: dict[str, Any], *, order: int, page_no: int) -> dict[str, Any]:
    mineru_type = str(raw.get("type") or "text")
    text = str(raw.get("text") or raw.get("content") or "").strip()
    caption = ""
    for key in _CAPTION_KEYS:
        cap = _caption_lines(raw.get(key))
        if cap:
            caption = cap
            break
    hint_id = str(raw.get("id") or f"p{page_no}_m{order}")
    item: dict[str, Any] = {
        "id": hint_id,
        "type": _map_hint_type(mineru_type, text or caption),
        "text": (text or caption)[:300],
        "bbox": _normalize_bbox(raw.get("bbox")),
        "mineru_type": mineru_type,
        "caption": (caption[:300] if caption else None),
    }
    return item


def _group_content_list(
    content_list: list[dict[str, Any]],
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    by_page_raw: dict[int, list[dict[str, Any]]] = defaultdict(list)
    type_histogram: dict[str, int] = {}
    for raw in content_list:
        if not isinstance(raw, dict):
            continue
        page_idx = raw.get("page_idx")
        if isinstance(page_idx, int) and page_idx >= 0:
            page_no = page_idx + 1
        else:
            page_no = 1
        by_page_raw[page_no].append(raw)
        mtype = str(raw.get("type") or "text")
        type_histogram[mtype] = type_histogram.get(mtype, 0) + 1

    by_page: dict[int, list[dict[str, Any]]] = {}
    for page_no, blocks in by_page_raw.items():
        hints: list[dict[str, Any]] = []
        for i, raw in enumerate(blocks):
            hints.append(_block_to_hint(raw, order=i, page_no=page_no))
        by_page[page_no] = hints

    has_aside = any(t in type_histogram for t in ("aside_text", "page_aside_text"))
    has_running_header = "header" in type_histogram
    has_footer = any(t in type_histogram for t in ("footer", "page_footnote", "page_number"))
    stats: dict[str, Any] = {
        "page_count": len(by_page),
        "type_histogram": type_histogram,
        "has_aside": has_aside,
        "has_running_header": has_running_header,
        "has_footer": has_footer,
        "pages_with_blocks": sorted(by_page.keys()),
    }
    return by_page, stats


def _put_json(key: str, obj: Any) -> None:
    assert_allowed_asset_key(key)
    put_import_bytes(
        key,
        json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )


def _patch_shared_upload_for_import35b() -> Callable[[], None]:
    """Route MinerU image uploads under import35b/ through the 35B asset helper.

    Shared ``import_assets`` only allows ``corpus/imports/``; LayoutStage must
    keep objects on the 35B prefix without crossing into corpus queues.
    """
    import app.services.import_assets as shared_assets

    orig = shared_assets.upload_import_asset

    def _routed(key: str, data: bytes, *, content_type: str) -> str:
        normalized = key.lstrip("/")
        if normalized.startswith("import35b/"):
            return upload_import_asset(key, data, content_type=content_type)
        return orig(key, data, content_type=content_type)

    shared_assets.upload_import_asset = _routed  # type: ignore[assignment]

    def _restore() -> None:
        shared_assets.upload_import_asset = orig  # type: ignore[assignment]

    return _restore


def run_mineru_layout(
    pdf_bytes: bytes,
    asset_prefix: str,
    progress_cb: Callable[[dict], None] | None = None,
) -> LayoutIndex:
    """Run MinerU Cloud on the full PDF and persist layout under import35b/.

    Raises on parse failure — caller decides fallback to pure 35B.
    """
    prefix = asset_prefix.rstrip("/")
    mineru_asset_prefix = f"{prefix}/35b/mineru/assets"

    def _progress(payload: dict) -> None:
        if not progress_cb:
            return
        try:
            merged = {"converter": "mineru_layout", **payload}
            if "converter" in payload and payload["converter"] == "mineru_cloud":
                merged["converter"] = "mineru_layout"
            progress_cb(merged)
        except Exception:  # noqa: BLE001
            pass

    if progress_cb:
        _progress({"ocr_progress": "parsing", "stage": "mineru_layout"})

    restore = _patch_shared_upload_for_import35b()
    try:
        _md, meta = convert_pdf_bytes(
            pdf_bytes,
            filename="import35b.pdf",
            engine="mineru_cloud",
            progress_cb=_progress,
            asset_prefix=mineru_asset_prefix,
        )
    except Exception:
        logger.exception("mineru_layout convert_pdf_bytes failed prefix=%s", prefix)
        raise
    finally:
        restore()

    content_list = meta.get("raw_blocks") or []
    if not isinstance(content_list, list):
        raise RuntimeError("mineru_layout: raw_blocks missing or not a list")

    by_page, stats = _group_content_list(content_list)
    stats["layout_engine"] = meta.get("engine") or "mineru_cloud"

    base = f"{prefix}/35b/mineru"
    _put_json(f"{base}/content_list.json", content_list)
    for page_no, hints in by_page.items():
        _put_json(f"{base}/by_page/p{page_no:04d}.json", hints)
    _put_json(f"{base}/layout_stats.json", stats)

    logger.info(
        "mineru_layout done pages=%d blocks=%d aside=%s header=%s footer=%s",
        stats.get("page_count"),
        len(content_list),
        stats.get("has_aside"),
        stats.get("has_running_header"),
        stats.get("has_footer"),
    )
    return LayoutIndex(by_page=by_page, stats=stats, content_list=content_list)


def load_page_hints(asset_prefix: str, page_no: int) -> list[dict[str, Any]] | None:
    """Load persisted MinerU hints for one page from MinIO."""
    prefix = asset_prefix.rstrip("/")
    key = f"{prefix}/35b/mineru/by_page/p{page_no:04d}.json"
    try:
        assert_allowed_asset_key(key)
    except ValueError:
        logger.warning("load_page_hints rejected key=%s", key)
        return None
    data = get_object_bytes(key)
    if not data:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("load_page_hints parse failed key=%s: %s", key, exc)
        return None
    if not isinstance(obj, list):
        return None
    return [x for x in obj if isinstance(x, dict)]
