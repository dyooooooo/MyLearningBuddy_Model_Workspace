"""Load per-page 35B pipeline debug artifacts from MinIO for Studio."""
from __future__ import annotations

import json
from typing import Any

from app.clients.minio_client import get_object_bytes
from app.db.models import Import35bJob
from app.import_35b.assets import asset_prefix_from_minio_key
from app.import_35b.stage_changelog import changelog_summary

# artifact id → filename under debug/p{NNNN}/
ARTIFACT_FILES: dict[str, str] = {
    "raw_model": "raw_model.json",
    "post_repair": "blueprint_post_repair.json",
    "post_mfr": "blueprint_post_mfr.json",
    "post_crop": "blueprint_post_crop.json",
    "blueprint": "blueprint.json",
    "html": "page.html",
    "ssr_report": "ssr_report.json",
    "meta": "debug.json",
    "changelog": "stage_changelog.json",
}

PAGE_IMAGE_FILE = "page.jpg"


def debug_page_base(job: Import35bJob, page_no: int) -> str:
    prefix = asset_prefix_from_minio_key(job.minio_key).rstrip("/")
    return f"{prefix}/35b/debug/p{page_no:04d}"


def _object_size(key: str) -> int | None:
    data = get_object_bytes(key)
    return len(data) if data else None


def _read_json(key: str) -> dict[str, Any] | list[Any] | None:
    data = get_object_bytes(key)
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:  # noqa: BLE001
        return None


def _artifact_index(base: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for artifact_id, filename in ARTIFACT_FILES.items():
        key = f"{base}/{filename}"
        size = _object_size(key)
        out[artifact_id] = {"available": size is not None, "size_bytes": size or 0}
    img_key = f"{base}/{PAGE_IMAGE_FILE}"
    img_size = _object_size(img_key)
    out["page_image"] = {"available": img_size is not None, "size_bytes": img_size or 0}
    return out


def load_page_debug_index(job: Import35bJob, page_no: int) -> dict[str, Any]:
    base = debug_page_base(job, page_no)
    artifacts = _artifact_index(base)
    meta = _read_json(f"{base}/debug.json")
    changelog_obj = _read_json(f"{base}/stage_changelog.json")
    cl_summary = changelog_summary(changelog_obj) if isinstance(changelog_obj, dict) else {
        "total_changes": 0,
        "summary_zh": "无 changelog（旧任务或未开启 debug）",
        "by_transition": {},
    }
    return {
        "job_id": job.id,
        "page_no": page_no,
        "debug_base": base,
        "artifacts": artifacts,
        "meta": meta if isinstance(meta, dict) else None,
        "changelog_summary": cl_summary,
    }


def load_page_debug_artifact(
    job: Import35bJob,
    page_no: int,
    artifact: str,
) -> tuple[str, bytes | dict[str, Any] | list[Any]]:
    """Return (content_type, body). Raises ValueError for unknown artifact."""
    if artifact not in ARTIFACT_FILES:
        raise ValueError(f"unknown artifact: {artifact}")
    base = debug_page_base(job, page_no)
    key = f"{base}/{ARTIFACT_FILES[artifact]}"
    data = get_object_bytes(key)
    if not data:
        raise FileNotFoundError(f"artifact not found: {artifact}")

    if artifact == "html":
        return "text/html; charset=utf-8", data

    if artifact in ("raw_model", "post_repair", "post_mfr", "post_crop", "blueprint", "ssr_report", "meta", "changelog"):
        try:
            return "application/json", json.loads(data)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid json for {artifact}") from exc

    return "application/octet-stream", data


def load_page_debug_image(job: Import35bJob, page_no: int) -> bytes | None:
    key = f"{debug_page_base(job, page_no)}/{PAGE_IMAGE_FILE}"
    return get_object_bytes(key)


def load_page_debug_changelog(job: Import35bJob, page_no: int) -> dict[str, Any] | None:
    obj = _read_json(f"{debug_page_base(job, page_no)}/stage_changelog.json")
    return obj if isinstance(obj, dict) else None
