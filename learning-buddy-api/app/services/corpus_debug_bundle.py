"""Per-page debug bundle for MinerU/corpus imports (MinIO under corpus/imports/).

Layout: ``{asset_prefix}/mineru/debug/p{NNNN}/``
  - page_blocks.json      (DocumentBlock IR)
  - page.html
  - debug.json           (T1 findings + meta)
  - page.jpg             (optional raster)
  - mineru_page.json     (Cloud content_list slice for this page)

Job-level Cloud originals (from zip) live under ``{asset_prefix}/mineru/``:
  content_list_v2 / content_list / layout / model — see mineru_raw_keys in job meta.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.clients.minio_client import get_object_bytes
from app.db.models import CorpusImportJob
from app.services.document_ir import DocumentBlock
from app.services.import_assets import asset_prefix_from_minio_key, upload_import_asset
from app.services.import_ir_validation import Finding, run_page_checks
from app.util.json_util import loads

logger = logging.getLogger(__name__)

ARTIFACT_FILES: dict[str, str] = {
    "blocks": "page_blocks.json",
    "html": "page.html",
    "meta": "debug.json",
    "mineru_page": "mineru_page.json",
}
# Job-level Cloud zip artifacts (not under pNNNN/)
CLOUD_ARTIFACT_KEYS = ("content_list_v2", "content_list", "layout", "middle", "model")
PAGE_IMAGE_FILE = "page.jpg"


def debug_page_base(job: CorpusImportJob, page_no: int) -> str:
    prefix = asset_prefix_from_minio_key(job.minio_key or "").rstrip("/")
    return f"{prefix}/mineru/debug/p{page_no:04d}"


def _job_meta(job: CorpusImportJob) -> dict[str, Any]:
    return loads(job.meta_json, {})


def _raw_keys(job: CorpusImportJob) -> dict[str, str]:
    meta = _job_meta(job)
    keys = meta.get("mineru_raw_keys") or {}
    return keys if isinstance(keys, dict) else {}


def load_cloud_content_list(job: CorpusImportJob) -> list[Any] | None:
    """Load full Cloud content_list (prefer v2) from MinIO via mineru_raw_keys."""
    keys = _raw_keys(job)
    for label in ("content_list_v2", "content_list"):
        key = keys.get(label)
        if not key:
            continue
        data = get_object_bytes(str(key))
        if not data:
            continue
        try:
            parsed = json.loads(data)
        except Exception:
            continue
        if isinstance(parsed, list):
            return parsed
    return None


def slice_cloud_page_blocks(
    job: CorpusImportJob,
    page_no: int,
    *,
    raw_blocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    """Return Cloud content_list blocks for one 1-based page.

    Accepts either in-memory ``raw_blocks`` (flat, with page_idx) or loads from
    MinIO. Supports flat v1 and page-grouped v2.
    """
    source: list[Any] | None = raw_blocks  # type: ignore[assignment]
    if source is None:
        source = load_cloud_content_list(job)
    if not source:
        return None

    # v2 page-grouped: [[page0...], [page1...]]
    if source and isinstance(source[0], list):
        idx = page_no - 1
        if 0 <= idx < len(source) and isinstance(source[idx], list):
            return [b for b in source[idx] if isinstance(b, dict)]
        return []

    # flat list with page_idx (0-based)
    out: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if isinstance(page_idx, int) and page_idx + 1 == page_no:
            out.append(item)
        elif page_idx is None and page_no == 1:
            # rare: no page_idx → treat as page 1 only when asking page 1
            out.append(item)
    return out


# Cap pretty-printed debug payloads so Studio UI does not freeze on book-level JSON.
_DEBUG_JSON_MAX_CHARS = 180_000


def slice_cloud_layout_page(parsed: Any, page_no: int) -> dict[str, Any] | None:
    """Extract one page from MinerU layout/middle JSON (``pdf_info``).

    Official shape: ``{ pdf_info: [{page_idx, ...}, ...], _backend, ... }``.
    Returns None if structure is not page-slicable.
    """
    page_idx = page_no - 1
    if isinstance(parsed, dict):
        pdf_info = parsed.get("pdf_info")
        if isinstance(pdf_info, list) and pdf_info:
            page_entry: dict[str, Any] | None = None
            for item in pdf_info:
                if isinstance(item, dict) and item.get("page_idx") == page_idx:
                    page_entry = item
                    break
            if page_entry is None and 0 <= page_idx < len(pdf_info):
                cand = pdf_info[page_idx]
                if isinstance(cand, dict):
                    page_entry = cand
            if page_entry is None:
                return {
                    "_slice": {
                        "page_no": page_no,
                        "page_idx": page_idx,
                        "total_pages": len(pdf_info),
                        "found": False,
                    },
                    "_backend": parsed.get("_backend"),
                    "_version_name": parsed.get("_version_name"),
                    "pdf_info": [],
                }
            return {
                "_slice": {
                    "page_no": page_no,
                    "page_idx": page_idx,
                    "total_pages": len(pdf_info),
                    "found": True,
                    "note": "book-level layout/middle 已按页切片，避免前端卡死",
                },
                "_backend": parsed.get("_backend"),
                "_version_name": parsed.get("_version_name"),
                "pdf_info": [page_entry],
            }
        # Some exports are a single page dict
        if "page_idx" in parsed or "para_blocks" in parsed:
            return {
                "_slice": {"page_no": page_no, "found": True, "note": "single-page layout"},
                **parsed,
            }
        return None

    if isinstance(parsed, list) and parsed:
        # list of page dicts
        if 0 <= page_idx < len(parsed) and isinstance(parsed[page_idx], dict):
            return {
                "_slice": {
                    "page_no": page_no,
                    "page_idx": page_idx,
                    "total_pages": len(parsed),
                    "found": True,
                },
                "pdf_info": [parsed[page_idx]],
            }
    return None


def _json_preview_payload(
    value: Any,
    *,
    artifact: str,
    minio_key: str = "",
    source: str = "",
    truncated_note: str = "",
) -> dict[str, Any]:
    """Return JSON for debug UI; fall back to truncated text if too large."""
    try:
        pretty = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        pretty = str(value)
    size = len(pretty.encode("utf-8"))
    out: dict[str, Any] = {
        "artifact": artifact,
        "content_type": "application/json",
        "size_bytes": size,
        "minio_key": minio_key,
    }
    if source:
        out["source"] = source
    if len(pretty) <= _DEBUG_JSON_MAX_CHARS:
        out["json"] = value
        return out
    # Prefer text preview — avoids shipping huge nested objects to the browser.
    head = pretty[:_DEBUG_JSON_MAX_CHARS]
    note = truncated_note or (
        f"/* truncated: showing first {_DEBUG_JSON_MAX_CHARS} chars of {size} bytes */\n"
    )
    out["content_type"] = "text/plain"
    out["text"] = note + head + "\n/* … truncated … */\n"
    out["truncated"] = True
    return out


def _layout_or_middle_key(job: CorpusImportJob) -> tuple[str, str]:
    """Return (label, minio_key) preferring layout.json, else middle.json."""
    raw = _raw_keys(job)
    for label in ("layout", "middle"):
        key = raw.get(label)
        if key:
            return label, str(key)
    return "", ""


def dump_page_debug(
    job: CorpusImportJob,
    *,
    page_no: int,
    blocks: list[DocumentBlock],
    html: str,
    page_jpeg: bytes | None = None,
    mineru_page_blocks: list[dict[str, Any]] | None = None,
    raw_blocks: list[dict[str, Any]] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write debug artifacts; return meta dict (includes findings)."""
    base = debug_page_base(job, page_no)
    page_blocks = [b for b in blocks if b.page_no == page_no]
    findings = run_page_checks(page_blocks, html, page_no=page_no)

    cloud_slice = mineru_page_blocks
    if cloud_slice is None:
        cloud_slice = slice_cloud_page_blocks(job, page_no, raw_blocks=raw_blocks)

    meta: dict[str, Any] = {
        "job_id": job.id,
        "page_no": page_no,
        "block_count": len(page_blocks),
        "blank": any(bool((b.meta or {}).get("blank")) for b in page_blocks),
        "findings": [f.model_dump() for f in findings],
        "finding_count": len(findings),
        "has_mineru_page": bool(cloud_slice),
        "mineru_page_block_count": len(cloud_slice or []),
        **(extra_meta or {}),
    }
    try:
        upload_import_asset(
            f"{base}/page_blocks.json",
            json.dumps(
                [b.model_dump() for b in page_blocks], ensure_ascii=False, indent=2
            ).encode("utf-8"),
            content_type="application/json",
        )
        upload_import_asset(
            f"{base}/page.html",
            (html or "").encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )
        upload_import_asset(
            f"{base}/debug.json",
            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        if page_jpeg:
            upload_import_asset(
                f"{base}/{PAGE_IMAGE_FILE}",
                page_jpeg,
                content_type="image/jpeg",
            )
        if cloud_slice is not None:
            upload_import_asset(
                f"{base}/mineru_page.json",
                json.dumps(cloud_slice, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
            )
    except Exception as exc:
        logger.warning("corpus debug dump failed job=%s page=%s: %s", job.id, page_no, exc)
    return meta


def _object_size(key: str) -> int | None:
    data = get_object_bytes(key)
    return len(data) if data else None


def _read_json(key: str) -> dict[str, Any] | list[Any] | None:
    data = get_object_bytes(key)
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


def load_page_debug_index(job: CorpusImportJob, page_no: int) -> dict[str, Any]:
    base = debug_page_base(job, page_no)
    artifacts: dict[str, dict[str, Any]] = {}
    for artifact_id, filename in ARTIFACT_FILES.items():
        key = f"{base}/{filename}"
        size = _object_size(key)
        artifacts[artifact_id] = {"available": size is not None, "size_bytes": size or 0}

    # mineru_page may be reconstructed on the fly from job-level Cloud JSON
    if not artifacts["mineru_page"]["available"]:
        sliced = slice_cloud_page_blocks(job, page_no)
        if sliced is not None:
            artifacts["mineru_page"] = {
                "available": True,
                "size_bytes": 0,
                "source": "cloud_raw_fallback",
            }

    img_key = f"{base}/{PAGE_IMAGE_FILE}"
    img_size = _object_size(img_key)
    artifacts["page_image"] = {
        "available": img_size is not None,
        "size_bytes": img_size or 0,
    }

    # Job-level Cloud zip originals
    raw = _raw_keys(job)
    for label in CLOUD_ARTIFACT_KEYS:
        key = raw.get(label)
        size = _object_size(str(key)) if key else None
        artifacts[f"cloud_{label}"] = {
            "available": size is not None,
            "size_bytes": size or 0,
            "minio_key": key or "",
        }

    # cloud_layout panel resolves layout OR middle (same MinerU middle.json family)
    layout_label, layout_key = _layout_or_middle_key(job)
    if layout_key:
        size = _object_size(layout_key)
        artifacts["cloud_layout"] = {
            "available": size is not None,
            "size_bytes": size or 0,
            "minio_key": layout_key,
            "resolved_as": layout_label,
            "page_sliced": True,
        }

    meta = _read_json(f"{base}/debug.json")
    return {
        "job_id": job.id,
        "page_no": page_no,
        "debug_base": base,
        "artifacts": artifacts,
        "meta": meta if isinstance(meta, dict) else None,
        "mineru_raw_keys": raw,
        "content_list_source": _job_meta(job).get("content_list_source"),
    }


def load_page_debug_artifact(
    job: CorpusImportJob, page_no: int, artifact: str
) -> dict[str, Any]:
    base = debug_page_base(job, page_no)

    if artifact == "page_image":
        key = f"{base}/{PAGE_IMAGE_FILE}"
        data = get_object_bytes(key)
        if not data:
            raise FileNotFoundError(artifact)
        return {
            "artifact": artifact,
            "content_type": "image/jpeg",
            "bytes_b64": base64.b64encode(data).decode("ascii"),
            "size_bytes": len(data),
        }

    # Job-level Cloud originals: cloud_content_list_v2 / cloud_layout / …
    # Always page-slice when possible — full book JSON freezes Studio.
    if artifact.startswith("cloud_"):
        label = artifact[len("cloud_") :]

        if artifact == "cloud_layout" or label in ("layout", "middle"):
            if artifact == "cloud_layout":
                resolved_label, key = _layout_or_middle_key(job)
            else:
                resolved_label, key = label, str(_raw_keys(job).get(label) or "")
            if not key:
                raise FileNotFoundError(artifact)
            data = get_object_bytes(str(key))
            if not data:
                raise FileNotFoundError(artifact)
            try:
                parsed = json.loads(data)
            except Exception:
                return {
                    "artifact": artifact,
                    "content_type": "application/octet-stream",
                    "text": data.decode("utf-8", errors="replace")[:_DEBUG_JSON_MAX_CHARS],
                    "minio_key": key,
                    "truncated": len(data) > _DEBUG_JSON_MAX_CHARS,
                }
            sliced = slice_cloud_layout_page(parsed, page_no)
            if sliced is not None:
                payload: Any = sliced
            elif isinstance(parsed, dict):
                # Never return full book body — keys-only stub.
                payload = {
                    "_warning": "layout/middle 无法按页切片；仅返回顶层键与体积",
                    "_top_keys": list(parsed.keys())[:80],
                    "_raw_bytes": len(data),
                    "_backend": parsed.get("_backend"),
                    "_version_name": parsed.get("_version_name"),
                }
            else:
                payload = {
                    "_warning": "无法按页切片",
                    "_type": type(parsed).__name__,
                    "_raw_bytes": len(data),
                }
            return _json_preview_payload(
                payload,
                artifact=artifact,
                minio_key=str(key),
                source=f"cloud_{resolved_label}_page_slice",
            )

        if label in ("content_list_v2", "content_list"):
            sliced = slice_cloud_page_blocks(job, page_no)
            if sliced is None:
                raise FileNotFoundError(artifact)
            key = _raw_keys(job).get(label) or ""
            return _json_preview_payload(
                {
                    "_slice": {
                        "page_no": page_no,
                        "block_count": len(sliced),
                        "note": "全书 content_list 已按页切片",
                    },
                    "blocks": sliced,
                },
                artifact=artifact,
                minio_key=str(key),
                source="cloud_content_list_page_slice",
            )

        key = _raw_keys(job).get(label)
        if not key:
            raise FileNotFoundError(artifact)
        data = get_object_bytes(str(key))
        if not data:
            raise FileNotFoundError(artifact)
        try:
            parsed = json.loads(data)
            return _json_preview_payload(
                parsed, artifact=artifact, minio_key=str(key), source="cloud_raw"
            )
        except Exception:
            return {
                "artifact": artifact,
                "content_type": "application/octet-stream",
                "text": data.decode("utf-8", errors="replace")[:_DEBUG_JSON_MAX_CHARS],
                "minio_key": key,
                "truncated": len(data) > _DEBUG_JSON_MAX_CHARS,
            }

    if artifact == "mineru_page":
        key = f"{base}/mineru_page.json"
        data = get_object_bytes(key)
        if data:
            return {
                "artifact": artifact,
                "content_type": "application/json",
                "json": json.loads(data),
                "source": "debug_file",
            }
        # Fallback: slice from full Cloud content_list and cache for next open
        sliced = slice_cloud_page_blocks(job, page_no)
        if sliced is None:
            raise FileNotFoundError(
                "mineru_page: Cloud content_list 未落盘（mineru_raw_keys 缺失）"
            )
        try:
            upload_import_asset(
                key,
                json.dumps(sliced, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
            )
        except Exception as exc:
            logger.warning("cache mineru_page failed page=%s: %s", page_no, exc)
        return {
            "artifact": artifact,
            "content_type": "application/json",
            "json": sliced,
            "source": "cloud_raw_fallback",
        }

    filename = ARTIFACT_FILES.get(artifact)
    if not filename:
        raise KeyError(f"unknown artifact: {artifact}")
    key = f"{base}/{filename}"
    data = get_object_bytes(key)
    if not data:
        raise FileNotFoundError(artifact)
    if filename.endswith(".json"):
        return {
            "artifact": artifact,
            "content_type": "application/json",
            "json": json.loads(data),
        }
    return {
        "artifact": artifact,
        "content_type": "text/html",
        "text": data.decode("utf-8", errors="replace"),
    }


def load_t1_snapshot(job: CorpusImportJob, page_no: int) -> list[dict[str, Any]]:
    meta = _read_json(f"{debug_page_base(job, page_no)}/debug.json")
    if not isinstance(meta, dict):
        return []
    findings = meta.get("findings")
    return findings if isinstance(findings, list) else []


_ = Finding
