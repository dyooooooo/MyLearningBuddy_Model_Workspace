"""Full teardown for a 35B import job — DB, MinIO, Redis queue, Milvus, pageaudit mirror."""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from app.clients.minio_client import delete_objects_by_prefix
from app.clients.redis_client import purge_import35b_queue
from app.config import settings
from app.import_35b.assets import job_minio_prefixes
from app.import_35b.caselaw import store as caselaw_vec_store

logger = logging.getLogger(__name__)


def purge_minio_for_job(*, minio_key: str, job_id: str) -> int:
    removed = 0
    for prefix in job_minio_prefixes(minio_key, job_id):
        n = delete_objects_by_prefix(prefix)
        if n:
            logger.info("import35b purge minio prefix=%s removed=%d", prefix, n)
        removed += n
    return removed


def purge_feedback_minio(asset_prefix: str) -> int:
    """Remove all feedback screenshots under a job asset prefix."""
    prefix = f"{asset_prefix.rstrip('/')}/35b/feedback/"
    return delete_objects_by_prefix(prefix)


def purge_pageaudit_mirror(job_id: str) -> dict:
    """Remove workspace debug mirror files tied to ``job_id`` (best-effort)."""
    root_raw = (settings.import35b_pageaudit_dir or "").strip()
    if not root_raw:
        return {"skipped": True, "reason": "import35b_pageaudit_dir unset"}
    root = Path(root_raw)
    if not root.is_dir():
        return {"skipped": True, "reason": "pageaudit dir missing"}

    removed_files = 0
    removed_dirs = 0

    job_dir = root / job_id
    if job_dir.is_dir():
        removed_files += sum(1 for p in job_dir.rglob("*") if p.is_file())
        shutil.rmtree(job_dir)
        removed_dirs += 1

    latest = root / "latest"
    meta_path = latest / "job_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        if meta.get("job_id") == job_id:
            for path in list(latest.iterdir()):
                if path.is_file():
                    path.unlink(missing_ok=True)
                    removed_files += 1

    archive = root / "archive"
    if archive.is_dir():
        for sub in list(archive.iterdir()):
            if not sub.is_dir():
                continue
            sub_meta = sub / "job_meta.json"
            if sub_meta.is_file():
                try:
                    meta = json.loads(sub_meta.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if meta.get("job_id") != job_id:
                    continue
            elif job_id not in sub.name:
                continue
            removed_files += sum(1 for p in sub.rglob("*") if p.is_file())
            shutil.rmtree(sub)
            removed_dirs += 1

    return {
        "skipped": False,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "root": str(root),
    }


def purge_external_assets(
    *,
    job_id: str,
    minio_key: str,
    caselaw_ids: list[str],
) -> dict:
    redis_removed = purge_import35b_queue(job_id)
    minio_removed = purge_minio_for_job(minio_key=minio_key, job_id=job_id)
    milvus_removed = caselaw_vec_store.delete_case_vectors(caselaw_ids)
    pageaudit = purge_pageaudit_mirror(job_id)
    return {
        "redis_queue": redis_removed,
        "minio_objects": minio_removed,
        "milvus_vectors": milvus_removed,
        "pageaudit": pageaudit,
    }
