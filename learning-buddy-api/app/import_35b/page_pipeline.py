"""Per-page atomic processing for the 35B import line."""
from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.clients.minio_client import put_import_bytes
from app.config import settings
from app.import_35b.asset_crop import crop_figure_assets
from app.import_35b.assets import assert_allowed_asset_key
from app.import_35b.blank_page import (
    compile_blank_page_html,
    is_blank_pdf_page,
    should_fallback_blank_page,
)
from app.import_35b.metrics import page_quality_metrics
from app.import_35b.mfr import enrich_equations_with_mfr
from app.import_35b.model_boundary import repair_blueprint_dict
from app.import_35b.normalize import normalize_blueprint
from app.import_35b.page_compiler import compile_page_html
from app.import_35b.raster import DEFAULT_DPI, PdfRasterCache
from app.import_35b.schemas import PageBlueprint
from app.import_35b.stage_changelog import build_stage_changelog, changelog_summary
from app.import_35b.validation import run_page_checks
from app.import_35b.vision_layout import PROMPT_VERSION, blueprint_to_dict, generate_page_blueprint

logger = logging.getLogger(__name__)


@dataclass
class PageResult:
    page_no: int
    blueprint: PageBlueprint | None
    html: str
    findings: list[dict]
    quality: dict
    status: str  # ok | blank_detected | blank_fallback | blueprint_failed
    debug_meta: dict
    elapsed_ms: int
    schema_errors: list[dict] = field(default_factory=list)


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


def _put_bytes(key: str, data: bytes, content_type: str) -> str:
    assert_allowed_asset_key(key)
    put_import_bytes(key, data, content_type=content_type)
    return key


def _raw_block_stats(raw_model: dict | None) -> dict[str, Any]:
    blocks = (raw_model or {}).get("blocks") or []
    kinds: dict[str, int] = {}
    empty_text = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        k = str(b.get("kind"))
        kinds[k] = kinds.get(k, 0) + 1
        if k == "text":
            t = (b.get("content") or {}).get("text")
            par = t.get("paragraphs") if isinstance(t, dict) else None
            if not (par and "".join(str(p) for p in par).strip()):
                empty_text += 1
    return {"blocks": len(blocks), "kinds": kinds, "text_empty": empty_text}


def _bp_block_stats(bp: PageBlueprint) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    empty_text = 0
    for b in bp.blocks:
        kinds[b.kind] = kinds.get(b.kind, 0) + 1
        if b.kind == "text" and not b.content.text_plain():
            empty_text += 1
    return {"blocks": len(bp.blocks), "kinds": kinds, "text_empty": empty_text}


def _debug_page_base(prefix: str, page_no: int) -> str:
    return f"{prefix}/35b/debug/p{page_no:04d}"


def _dump_stage_json(prefix: str, page_no: int, filename: str, obj: Any) -> None:
    try:
        _put_json(f"{_debug_page_base(prefix, page_no)}/{filename}", obj)
    except Exception as exc:  # noqa: BLE001
        logger.warning("35b stage dump %s page=%d failed: %s", filename, page_no, exc)


def _dump_page_debug(
    prefix: str,
    page_no: int,
    *,
    jpeg: bytes | None,
    dbg: dict[str, Any],
    blueprint: PageBlueprint | None,
    html: str | None,
    meta: dict[str, Any],
) -> None:
    base = _debug_page_base(prefix, page_no)
    try:
        if jpeg:
            _put_bytes(f"{base}/page.jpg", jpeg, "image/jpeg")
        if "raw_model" in dbg:
            _put_json(f"{base}/raw_model.json", dbg["raw_model"])
        if blueprint is not None:
            _put_json(f"{base}/blueprint.json", blueprint_to_dict(blueprint))
        _put_html(f"{base}/page.html", html or "")
        _put_json(f"{base}/debug.json", meta)
    except Exception as exc:  # noqa: BLE001
        logger.warning("35b debug dump failed page=%d: %s", page_no, exc)


def _postprocess_blueprint(
    bp: PageBlueprint,
    *,
    pdf_bytes: bytes,
    asset_prefix: str,
    profile_id: str,
    page_no: int,
    prefix: str,
    debug_on: bool,
    dbg: dict[str, Any],
    page_t0: float,
) -> PageResult:
    """Shared mfr → crop → normalize → compile → T1 path."""
    stage_snapshots: dict[str, Any] = {}
    if debug_on and dbg.get("raw_model") is not None:
        stage_snapshots["raw_model"] = dbg["raw_model"]
    if debug_on and dbg.get("repaired_model") is not None:
        stage_snapshots["repaired_model"] = dbg["repaired_model"]
        _dump_stage_json(prefix, page_no, "blueprint_post_boundary.json", dbg["repaired_model"])

    bp_post_repair = blueprint_to_dict(bp)
    if debug_on:
        stage_snapshots["post_repair"] = bp_post_repair
        _dump_stage_json(prefix, page_no, "blueprint_post_repair.json", bp_post_repair)

    bp = enrich_equations_with_mfr(pdf_bytes=pdf_bytes, blueprint=bp)
    bp_post_mfr = blueprint_to_dict(bp)
    if debug_on:
        stage_snapshots["post_mfr"] = bp_post_mfr
        _dump_stage_json(prefix, page_no, "blueprint_post_mfr.json", bp_post_mfr)

    bp = crop_figure_assets(
        pdf_bytes=pdf_bytes,
        blueprint=bp,
        asset_prefix=asset_prefix,
    )
    bp_post_crop = blueprint_to_dict(bp)
    if debug_on:
        stage_snapshots["post_crop"] = bp_post_crop
        _dump_stage_json(prefix, page_no, "blueprint_post_crop.json", bp_post_crop)

    bp = normalize_blueprint(bp)
    bp_final = blueprint_to_dict(bp)
    if debug_on:
        stage_snapshots["blueprint"] = bp_final

    _put_json(f"{prefix}/35b/blueprints/p{page_no:04d}.json", bp_final)

    ssr_report: list[dict] = []
    html = compile_page_html(bp, profile_id=profile_id, ssr_report=ssr_report)
    if debug_on:
        _dump_stage_json(prefix, page_no, "ssr_report.json", ssr_report)
        changelog = build_stage_changelog(
            page_no,
            stage_snapshots,
            boundary_repairs=dbg.get("boundary_repairs") or [],
        )
        _dump_stage_json(prefix, page_no, "stage_changelog.json", changelog)
    else:
        changelog = None

    try:
        findings = [f.model_dump() for f in run_page_checks(bp, html, ssr_report=ssr_report)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("35b page %d validation failed: %s", page_no, exc)
        findings = []

    quality = page_quality_metrics(bp, html)
    elapsed_ms = int((time.perf_counter() - page_t0) * 1000)
    raw_stats = _raw_block_stats(dbg.get("raw_model")) if debug_on else None
    debug_meta: dict[str, Any] = {
        "page_no": page_no,
        "status": "ok",
        "prompt_version": PROMPT_VERSION,
        "used_json_schema": settings.vllm_use_json_schema,
        "mfr_enabled": settings.mfr_enabled,
        "call": {k: dbg.get(k) for k in ("used_schema", "finish_reason", "usage", "max_tokens")},
        "truncated": dbg.get("finish_reason") == "length",
        "raw": raw_stats,
        "final": _bp_block_stats(bp),
        "boundary_repairs": dbg.get("boundary_repairs") or [],
        "findings": findings,
        "elapsed_ms": elapsed_ms,
        "stages_available": {
            "raw_model": stage_snapshots.get("raw_model") is not None,
            "repaired_model": stage_snapshots.get("repaired_model") is not None,
            "post_repair": "post_repair" in stage_snapshots,
            "post_mfr": "post_mfr" in stage_snapshots,
            "post_crop": "post_crop" in stage_snapshots,
            "blueprint": "blueprint" in stage_snapshots,
            "html": True,
            "ssr_report": True,
            "changelog": changelog is not None,
        },
        "changelog_summary": changelog_summary(changelog or {}) if changelog else {},
        "quality": quality,
    }
    return PageResult(
        page_no=page_no,
        blueprint=bp,
        html=html,
        findings=findings,
        quality=quality,
        status="ok",
        debug_meta=debug_meta,
        elapsed_ms=elapsed_ms,
        schema_errors=[],
    )


def process_page(
    *,
    page_no: int,
    pdf_bytes: bytes,
    raster: PdfRasterCache,
    asset_prefix: str,
    profile_id: str = "hujiao_math_hs_v1",
    layout_hints: list[dict] | None = None,
    profile: dict | None = None,
    dpi: int = DEFAULT_DPI,
    debug_on: bool | None = None,
    from_raw: dict | None = None,
) -> PageResult:
    """Process a single PDF page into blueprint + HTML (page-atomic core)."""
    page_t0 = time.perf_counter()
    prefix = asset_prefix.rstrip("/")
    if debug_on is None:
        debug_on = settings.import35b_debug_dump
    schema_errors: list[dict] = []
    dbg: dict[str, Any] = {}

    if is_blank_pdf_page(raster, page_no):
        bp, html = compile_blank_page_html(page_no, profile_id=profile_id)
        _put_json(f"{prefix}/35b/blueprints/p{page_no:04d}.json", blueprint_to_dict(bp))
        quality = page_quality_metrics(bp, html)
        elapsed_ms = int((time.perf_counter() - page_t0) * 1000)
        meta = {
            "page_no": page_no,
            "status": "blank_detected",
            "prompt_version": PROMPT_VERSION,
            "final": _bp_block_stats(bp),
            "elapsed_ms": elapsed_ms,
        }
        if debug_on:
            _dump_page_debug(prefix, page_no, jpeg=None, dbg={}, blueprint=bp, html=html, meta=meta)
        return PageResult(
            page_no=page_no,
            blueprint=bp,
            html=html,
            findings=[],
            quality=quality,
            status="blank_detected",
            debug_meta=meta,
            elapsed_ms=elapsed_ms,
            schema_errors=[],
        )

    jpeg = raster.page_jpeg(page_no, dpi=dpi)
    bp: PageBlueprint | None = None
    last_page_err: Exception | None = None

    if from_raw is not None:
        try:
            raw = copy.deepcopy(from_raw)
            if debug_on:
                dbg["raw_model"] = copy.deepcopy(raw)
            raw, boundary_repairs = repair_blueprint_dict(raw)
            if debug_on:
                dbg["boundary_repairs"] = boundary_repairs
                dbg["repaired_model"] = copy.deepcopy(raw)
            raw.setdefault("page_no", page_no)
            raw.setdefault("page_size", [1000, 1000])
            raw.pop("column_count_hint", None)
            bp = PageBlueprint.model_validate(raw)
            if bp.page_no != page_no:
                raise ValueError(
                    f"blueprint page_no mismatch: expected {page_no}, got {bp.page_no}"
                )
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            last_page_err = exc
            schema_errors.append({"page_no": page_no, "attempt": 1, "error": str(exc)[:500]})
            bp = None
    else:
        for attempt in range(2):
            try:
                bp = generate_page_blueprint(
                    page_no=page_no,
                    page_jpeg=jpeg,
                    layout_hints=layout_hints,
                    profile=profile,
                    debug=dbg if debug_on else None,
                )
                break
            except (ValueError, json.JSONDecodeError) as exc:
                last_page_err = exc
                schema_errors.append(
                    {"page_no": page_no, "attempt": attempt + 1, "error": str(exc)[:500]}
                )
                logger.warning(
                    "35b page %d blueprint attempt %d failed: %s",
                    page_no,
                    attempt + 1,
                    exc,
                )

    if bp is None:
        elapsed_ms = int((time.perf_counter() - page_t0) * 1000)
        meta = {
            "page_no": page_no,
            "status": "blueprint_failed",
            "prompt_version": PROMPT_VERSION,
            "used_json_schema": settings.vllm_use_json_schema,
            "call": {k: dbg.get(k) for k in ("used_schema", "finish_reason", "usage", "max_tokens")},
            "truncated": dbg.get("finish_reason") == "length",
            "raw": _raw_block_stats(dbg.get("raw_model")),
            "attempt_errors": schema_errors,
            "elapsed_ms": elapsed_ms,
        }
        if debug_on:
            _dump_page_debug(
                prefix, page_no, jpeg=jpeg, dbg=dbg, blueprint=None, html=None, meta=meta
            )
        if should_fallback_blank_page(raster, page_no, dpi=dpi, err=last_page_err):
            bp, html = compile_blank_page_html(page_no, profile_id=profile_id)
            quality = page_quality_metrics(bp, html)
            blank_meta = {
                **meta,
                "status": "blank_fallback",
                "final": _bp_block_stats(bp),
            }
            return PageResult(
                page_no=page_no,
                blueprint=bp,
                html=html,
                findings=[],
                quality=quality,
                status="blank_fallback",
                debug_meta=blank_meta,
                elapsed_ms=elapsed_ms,
                schema_errors=schema_errors,
            )
        raise last_page_err or RuntimeError(f"page {page_no} blueprint failed")

    result = _postprocess_blueprint(
        bp,
        pdf_bytes=pdf_bytes,
        asset_prefix=asset_prefix,
        profile_id=profile_id,
        page_no=page_no,
        prefix=prefix,
        debug_on=debug_on,
        dbg=dbg,
        page_t0=page_t0,
    )
    result.schema_errors = schema_errors
    result.debug_meta["attempt_errors"] = schema_errors
    if debug_on:
        _dump_page_debug(
            prefix,
            page_no,
            jpeg=jpeg,
            dbg=dbg,
            blueprint=result.blueprint,
            html=result.html,
            meta=result.debug_meta,
        )
    return result


def compile_from_blueprint(
    bp: PageBlueprint,
    *,
    pdf_bytes: bytes,
    asset_prefix: str,
    profile_id: str,
    page_no: int,
    debug_on: bool = False,
) -> PageResult:
    """Skip VLM; run mfr/crop/normalize/compile/T1 from an existing blueprint."""
    page_t0 = time.perf_counter()
    prefix = asset_prefix.rstrip("/")
    result = _postprocess_blueprint(
        bp,
        pdf_bytes=pdf_bytes,
        asset_prefix=asset_prefix,
        profile_id=profile_id,
        page_no=page_no,
        prefix=prefix,
        debug_on=debug_on,
        dbg={},
        page_t0=page_t0,
    )
    if debug_on:
        _dump_page_debug(
            prefix,
            page_no,
            jpeg=None,
            dbg={},
            blueprint=result.blueprint,
            html=result.html,
            meta=result.debug_meta,
        )
    return result
