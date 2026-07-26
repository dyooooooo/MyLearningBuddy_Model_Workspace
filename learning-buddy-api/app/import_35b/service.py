"""Isolated orchestration for the 35B layout import line.

Replaces the ``vision_layout_35b`` branch that used to live in
``corpus_import.process_import_job``. This line does NOT build the corpus tree
(textbook/section/semantic nodes), does NOT run structure_merge, and does NOT
touch corpus_nodes. It only: read PDF -> run 35B pipeline page-by-page ->
persist into import35b_pages (idempotent per textbook_key) -> mark job done.

The only shared runtime dependency reached from here is the 35B vLLM service
(via app.import_35b.vision_layout) and low-level infra (MinIO, DB, Redis).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes, put_import_bytes
from app.db.models import Import35bJob
from app.db.session import get_session_factory
from app.import_35b import store
from app.import_35b.assets import (
    IMPORT35B_PREFIX,
    asset_prefix_from_minio_key,
    import35b_pdf_key,
)
from app.import_35b.pipeline import run_35b_import_pipeline
from app.import_35b.purge import purge_external_assets
from app.import_35b.render_zone import html_to_plain_text

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_import(
    session: Session,
    *,
    minio_key: str,
    subject: str = "math",
    grade_band: str = "hs",
    label: str = "",
    profile_id: str = "hujiao_math_hs_v1",
    page_limit: int | None = None,
) -> Import35bJob:
    """Create an import35b job row (status=uploaded). Enqueue happens in route.

    Enforces MinIO isolation: if the uploaded PDF is not already under the
    ``import35b/`` prefix, copy it there (``import35b/{job}/source.pdf``) so this
    line never reads/keeps objects in MinerU's ``corpus/imports/`` space.
    """
    job = store.create_job(
        session,
        minio_key=minio_key,
        subject=subject,
        grade_band=grade_band,
        label=label,
        profile_id=profile_id,
        page_limit=page_limit,
    )
    if not (minio_key or "").lstrip("/").startswith(IMPORT35B_PREFIX):
        pdf_bytes = get_object_bytes(minio_key)
        if pdf_bytes:
            isolated_key = import35b_pdf_key(job.id)
            put_import_bytes(isolated_key, pdf_bytes, content_type="application/pdf")
            job.minio_key = isolated_key
            logger.info("import35b re-keyed PDF into isolated prefix %s", isolated_key)
    logger.info(
        "import35b queued id=%s label=%r key=%s tb=%s",
        job.id,
        job.label,
        job.minio_key,
        job.textbook_key,
    )
    return job


def purge_import_job(session: Session, job_id: str) -> dict | None:
    """Delete a 35B job and erase all associated artifacts (MinIO, DB, queue, pageaudit)."""
    from app.services import pipeline_telemetry

    job = store.get_job(session, job_id)
    if job is None:
        pipeline_telemetry.request_cancel(job_id)
        from app.clients.redis_client import purge_import35b_queue

        purge_import35b_queue(job_id)
        return None

    minio_key = job.minio_key
    # Signal worker before tearing down row/assets so in-flight convert stops.
    pipeline_telemetry.request_cancel(job_id)
    store.set_status(session, job, "canceled")
    session.commit()

    db_counts = store.purge_job_data(session, job)
    session.commit()

    external = purge_external_assets(
        job_id=job_id,
        minio_key=minio_key,
        caselaw_ids=db_counts.get("caselaw_ids") or [],
    )

    public_db = {k: v for k, v in db_counts.items() if k != "caselaw_ids"}
    logger.info(
        "import35b purged id=%s db=%s external=%s",
        job_id,
        public_db,
        external,
    )
    return {
        "deleted": True,
        "id": job_id,
        "purge": {**external, "db": public_db},
    }


def run_convert(job_id: str) -> None:
    """Worker entrypoint. Idempotent per textbook_key.

    Uses short-lived sessions: one to load/mark, one to purge old pages, one per
    page (live preview), one to finalize — so a long conversion never holds a
    single transaction open.
    """
    factory = get_session_factory()

    with factory() as session:
        job = store.get_job(session, job_id)
        if job is None:
            logger.info("import35b worker skip — job gone id=%s", job_id)
            return
        if job.status in ("done", "failed", "canceled"):
            logger.info("import35b worker skip — terminal status=%s id=%s", job.status, job_id)
            return
        textbook_key = job.textbook_key
        minio_key = job.minio_key
        profile_id = job.profile_id or "hujiao_math_hs_v1"
        page_limit = job.page_limit
        subject = job.subject or "math"
        grade_band = job.grade_band or "hs"
        store.set_status(session, job, "converting")
        session.commit()

    try:
        pdf_bytes = get_object_bytes(minio_key)
        if not pdf_bytes:
            raise ValueError(f"PDF not found in storage: {minio_key}")

        asset_prefix = asset_prefix_from_minio_key(minio_key)

        # Idempotency reset: drop all previously-stored pages of this textbook
        # BEFORE writing the fresh run, so a shorter reimport leaves no residue.
        with factory() as session:
            purged = store.delete_pages(session, textbook_key)
            session.commit()
            if purged:
                logger.info("import35b purged %d stale pages tb=%s", purged, textbook_key)

        def _job_active(s) -> bool:
            j = store.get_job(s, job_id)
            return j is not None and j.status not in ("canceled",)

        def _cancel_check() -> None:
            from app.services.pipeline_telemetry import ImportCanceled, raise_if_canceled

            raise_if_canceled(job_id)
            with factory() as s:
                if not _job_active(s):
                    raise ImportCanceled(f"import35b job aborted: {job_id}")

        def _page_cb(page_no: int, html: str, total: int) -> None:
            _cancel_check()
            with factory() as s:
                if not _job_active(s):
                    from app.services.pipeline_telemetry import ImportCanceled

                    raise ImportCanceled(f"import35b job aborted: {job_id}")
                store.upsert_page(
                    s,
                    textbook_key=textbook_key,
                    job_id=job_id,
                    page_no=page_no,
                    html=html,
                    plain_text=html_to_plain_text(html),
                )
                j = store.get_job(s, job_id)
                if j is not None:
                    store.patch_meta(
                        s,
                        j,
                        {"progress": f"{page_no}/{total}", "page": page_no, "total": total},
                    )
                s.commit()

        try:
            _markdown, meta = run_35b_import_pipeline(
                pdf_bytes=pdf_bytes,
                asset_prefix=asset_prefix,
                page_html_cb=_page_cb,
                progress_cb=lambda _p: _cancel_check(),
                profile_id=profile_id,
                page_limit=page_limit,
            )
        except Exception as exc:
            from app.services.pipeline_telemetry import ImportCanceled

            if isinstance(exc, ImportCanceled):
                logger.info("import35b canceled id=%s", job_id)
                with factory() as session:
                    job = store.get_job(session, job_id)
                    if job is not None:
                        store.set_status(session, job, "canceled", error="已取消（用户删除或停止）")
                        session.commit()
                return
            raise

        with factory() as session:
            job = store.get_job(session, job_id)
            if job is None:
                logger.info("import35b finalize skip — job gone id=%s", job_id)
                return
            if job.status == "canceled":
                logger.info("import35b finalize skip — canceled id=%s", job_id)
                return
            job.page_count = int(meta.get("page_count", 0))
            store.set_meta(session, job, meta)
            store.set_status(session, job, "done")
            session.commit()
        logger.info(
            "import35b done id=%s pages=%d tb=%s",
            job_id,
            int(meta.get("page_count", 0)),
            textbook_key,
        )
    except Exception as exc:  # noqa: BLE001 — record failure on the job row
        logger.exception("import35b failed id=%s", job_id)
        with factory() as session:
            job = store.get_job(session, job_id)
            if job is not None:
                store.set_status(session, job, "failed", error=str(exc))
                session.commit()
    finally:
        from app.services import pipeline_telemetry

        pipeline_telemetry.clear_cancel(job_id)
