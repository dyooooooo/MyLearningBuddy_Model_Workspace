"""End-to-end 35B import experiment pipeline."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from app.clients.minio_client import put_import_bytes
from app.config import settings
from app.import_35b.assets import assert_allowed_asset_key
from app.import_35b.metrics import aggregate_quality
from app.import_35b.mineru_layout import LayoutIndex, run_mineru_layout
from app.import_35b.page_pipeline import process_page
from app.import_35b.profile_features import merge_runtime_profile
from app.import_35b.raster import DEFAULT_DPI, PdfRasterCache
from app.import_35b.render_zone import html_to_plain_text
from app.import_35b.schemas import PageBlueprint
from app.import_35b.vision_layout import PROMPT_VERSION, blueprint_to_dict

logger = logging.getLogger(__name__)


def _put_json(key: str, obj: Any) -> str:
    assert_allowed_asset_key(key)
    put_import_bytes(
        key,
        json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    return key


def _put_html(key: str, html: str) -> str:
    assert_allowed_asset_key(key)
    put_import_bytes(key, html.encode("utf-8"), content_type="text/html; charset=utf-8")
    return key


def _merge_markdown(page_html: dict[int, str]) -> str:
    parts = []
    for page_no in sorted(page_html):
        text = html_to_plain_text(page_html[page_no]).strip()
        if text:
            parts.append(f"<!-- page:{page_no} -->\n{text}")
    return "\n\n---\n\n".join(parts)


def _zone_counts(blueprints: list[PageBlueprint]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for bp in blueprints:
        for zone, count in bp.zone_counts().items():
            counts[zone] = counts.get(zone, 0) + count
    return counts


def run_35b_import_pipeline(
    *,
    pdf_bytes: bytes,
    asset_prefix: str,
    progress_cb: Callable[[dict], None] | None = None,
    page_html_cb: Callable[[int, str, int], None] | None = None,
    profile_id: str = "hujiao_math_hs_v1",
    dpi: int = DEFAULT_DPI,
    page_limit: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run the 35B experiment and return (markdown-ish text, meta)."""
    t0 = time.perf_counter()
    prefix = asset_prefix.rstrip("/")
    debug_on = settings.import35b_debug_dump
    blueprints: list[PageBlueprint] = []
    page_html: dict[int, str] = {}
    quality_by_page: list[dict] = []
    schema_validation_errors_by_page: list[dict] = []
    blank_pages: list[int] = []
    debug_manifest: list[dict] = []

    fallback_from: str | None = None
    layout_index = LayoutIndex()
    if progress_cb:
        from app.services.pipeline_telemetry import ImportCanceled

        try:
            progress_cb(
                {
                    "ocr_progress": "parsing",
                    "converter": "mineru_layout",
                    "stage": "mineru_layout",
                }
            )
        except ImportCanceled:
            raise
        except Exception:  # noqa: BLE001
            pass
    try:
        layout_index = run_mineru_layout(
            pdf_bytes, asset_prefix=prefix, progress_cb=progress_cb
        )
    except Exception as exc:  # noqa: BLE001 — degrade to pure 35B
        from app.services.pipeline_telemetry import ImportCanceled

        if isinstance(exc, ImportCanceled):
            raise
        fallback_from = "mineru_cloud"
        layout_index = LayoutIndex()
        logger.warning("35b mineru_layout failed — fallback pure 35B: %s", exc)

    profile = merge_runtime_profile(profile_id, layout_index.stats or None)
    layout_engine = (
        "pure_35b"
        if fallback_from
        else (layout_index.stats.get("layout_engine") or "mineru_cloud")
    )

    with PdfRasterCache(pdf_bytes) as raster:
        total_pages = raster.page_count()
        process_pages = min(total_pages, page_limit) if page_limit else total_pages
        for page_no in range(1, process_pages + 1):
            if progress_cb:
                progress_cb(
                    {
                        "ocr_progress": f"{page_no}/{process_pages}",
                        "ocr_page": page_no,
                        "ocr_total": process_pages,
                        "converter": "vision_layout_35b",
                    }
                )
            page_t0 = time.perf_counter()
            hints = layout_index.by_page.get(page_no) or None
            result = process_page(
                page_no=page_no,
                pdf_bytes=pdf_bytes,
                raster=raster,
                asset_prefix=prefix,
                profile_id=profile_id,
                layout_hints=hints,
                profile=profile,
                dpi=dpi,
                debug_on=debug_on,
            )
            schema_validation_errors_by_page.extend(result.schema_errors)
            if result.status in ("blank_detected", "blank_fallback"):
                blank_pages.append(page_no)
            if result.blueprint is not None:
                blueprints.append(result.blueprint)
                quality_by_page.append(result.quality)
                page_html[page_no] = result.html
                if page_html_cb:
                    page_html_cb(page_no, result.html, process_pages)
            if debug_on and result.debug_meta:
                debug_manifest.append(result.debug_meta)

            blocks_n = len(result.blueprint.blocks) if result.blueprint else 0
            logger.info(
                "35b import page %d/%d status=%s blocks=%d elapsed=%.2fs",
                page_no,
                process_pages,
                result.status,
                blocks_n,
                time.perf_counter() - page_t0,
            )

    if debug_on:
        try:
            _put_json(f"{prefix}/35b/debug/manifest.json", debug_manifest)
        except Exception as exc:  # noqa: BLE001
            logger.warning("35b debug manifest dump failed: %s", exc)
    blueprints_key = _put_json(
        f"{prefix}/35b/blueprints/pages.json",
        [blueprint_to_dict(bp) for bp in blueprints],
    )
    html_keys: dict[str, str] = {}
    for page_no, html in page_html.items():
        html_keys[str(page_no)] = _put_html(f"{prefix}/35b/pages/p{page_no:04d}.html", html)

    markdown = _merge_markdown(page_html)
    quality = aggregate_quality(quality_by_page)
    meta: dict[str, Any] = {
        "converter": "vision_layout_35b",
        "engine": "vision_layout_35b",
        "pdf_kind": "vision_layout_35b",
        "has_structure": True,
        "content_format": "html",
        "page_count": len(blueprints),
        "pdf_total_pages": total_pages,
        "ocr_total": len(blueprints),
        "char_count": len(markdown),
        "prompt_version": PROMPT_VERSION,
        "layout_engine": layout_engine,
        "layout_stats": layout_index.stats or {},
        "features": profile.get("features") or {},
        "fallback_from": fallback_from,
        "blueprints_key": blueprints_key,
        "page_html_keys": html_keys,
        "block_count": sum(len(bp.blocks) for bp in blueprints),
        "block_count_by_zone": _zone_counts(blueprints),
        "html_block_coverage": quality["html_block_coverage"],
        "html_block_coverage_by_page": [p["html_block_coverage"] for p in quality_by_page],
        "content_quality": quality,
        "content_quality_by_page": quality_by_page,
        "schema_validation_errors_by_page": schema_validation_errors_by_page,
        "blank_pages": blank_pages,
        "debug_prefix": f"{prefix}/35b/debug" if debug_on else None,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }
    return markdown, meta
