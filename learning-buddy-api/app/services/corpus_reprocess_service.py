"""Page / block reprocess for MinerU corpus line.

Architecture:
  - Full-book import: MinerU Cloud only (profile ``refine_mode=skip``) — no 35B.
  - User page/block re-ID: previous IR + page JPEG → 35B — **no MinerU re-call**.

Modes:
  - ``restore_mineru`` / ``mineru_rerecognize``: restore working IR from MinerU
    baseline (seed via from_cloud once); baseline never overwritten by 35B
  - ``content_correct``: text/latex only; topology frozen
  - ``layout_content`` / ``refine_35b``: patch text+zone+role on existing ids
  - ``layout_rebuild``: 35B rebuild + align + judge + fuse (aggressive;
    roll back with restore_mineru)
  - ``rerender`` / ``from_blocks``: recompile HTML from stored IR
  - ``from_cloud``: ops — re-adapt from Cloud content_list; seeds baseline if missing
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.db.models import CorpusImportJob
from app.services.corpus_blank_page import mark_blank_html
from app.services.corpus_debug_bundle import dump_page_debug, slice_cloud_page_blocks
from app.services.corpus_import_incremental import upsert_page_draft_chunk
from app.services.document_ir import DocumentBlock, render_page_html
from app.services.import_assets import asset_prefix_from_minio_key
from app.services.import_ir_store import load_blocks, patch_job_ir_meta, save_blocks
from app.services.ir.apply_refine import apply_page_refine, count_effective_changes
from app.services.ir.layout_enrich import enrich_page_blocks_slim
from app.services.ir.math_enrich_blocks import enrich_page_blocks_math
from app.services.ir.mineru_adapter import mineru_content_list_to_ir
from app.services.ir.mineru_baseline import (
    baseline_meta_stub,
    ensure_mineru_baseline,
    load_mineru_baseline,
    save_mineru_baseline,
)
from app.services.ir.mineru_page_refine import refine_one_block, refine_page
from app.services.ir.pdf_render_cache import PdfRenderCache
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile
from app.services.ir.vision_adapter import resolve_asset_urls
from app.util.json_util import loads

logger = logging.getLogger(__name__)

ReprocessMode = Literal[
    "restore_mineru",
    "mineru_rerecognize",
    "content_correct",
    "layout_content",
    "layout_rebuild",
    "refine_35b",
    "rerender",
    "from_blocks",
    "from_cloud",
]

_REFINE_MODES = frozenset(
    {"content_correct", "layout_content", "layout_rebuild", "refine_35b"}
)


def normalize_reprocess_mode(mode: str) -> str:
    """Map legacy aliases to canonical modes."""
    m = (mode or "").strip() or "layout_content"
    if m == "refine_35b":
        return "layout_content"
    if m == "mineru_rerecognize":
        return "restore_mineru"
    return m


def _rebuild_page_from_cloud(
    job: CorpusImportJob,
    page_no: int,
    *,
    engine: str,
) -> list[DocumentBlock] | None:
    """Re-adapt one page from persisted Cloud content_list (ops only)."""
    raw = slice_cloud_page_blocks(job, page_no)
    if not raw:
        return None
    enriched: list[dict[str, Any]] = []
    for b in raw:
        if not isinstance(b, dict):
            continue
        item = dict(b)
        if not isinstance(item.get("page_idx"), int):
            item["page_idx"] = page_no - 1
        enriched.append(item)
    if not enriched:
        return None
    prefix = ""
    if job.minio_key:
        prefix = asset_prefix_from_minio_key(job.minio_key)
    page_blocks = mineru_content_list_to_ir(
        enriched, engine=engine, asset_prefix=prefix
    )
    return resolve_asset_urls(page_blocks)


def _commit_durable(
    session: Session,
    *,
    what: str,
    job_id: str,
    job: CorpusImportJob | None = None,
) -> None:
    """Force a Postgres commit so UI/reload see the update immediately."""
    try:
        session.commit()
        if job is not None:
            try:
                session.refresh(job)
            except Exception:
                pass
        logger.info("durable persist ok job=%s what=%s", job_id, what)
    except Exception:
        session.rollback()
        logger.exception("durable persist failed job=%s what=%s", job_id, what)
        raise


def _save_blocks(
    session: Session,
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
    *,
    durable: bool = False,
) -> str:
    """Write blocks.json to MinIO and patch job meta. Optionally commit now."""
    key = save_blocks(job, blocks)
    patch_job_ir_meta(job, blocks, key)
    session.add(job)
    session.flush()
    if durable:
        _commit_durable(
            session, what="blocks.json+meta", job_id=job.id, job=job
        )
    return key


def _finalize_and_persist(
    session: Session,
    job: CorpusImportJob,
    *,
    blocks: list[DocumentBlock],
    page_no: int,
    page_blocks: list[DocumentBlock],
    profile_id: str,
    engine: str,
    total_pages: int,
    mode: str,
    extra_meta: dict[str, Any] | None = None,
    page_jpeg: bytes | None = None,
    cache: PdfRenderCache | None = None,
    durable: bool = True,
) -> dict[str, Any]:
    html = render_page_html(blocks, page_no, profile_id=profile_id)
    if any(bool((b.meta or {}).get("blank")) for b in page_blocks):
        html = mark_blank_html(html)

    if page_jpeg is None and cache is not None:
        try:
            page_jpeg = cache.page_jpeg(page_no, dpi=120)
        except Exception as exc:
            logger.warning("page jpeg failed job=%s page=%s: %s", job.id, page_no, exc)

    debug_meta = dump_page_debug(
        job,
        page_no=page_no,
        blocks=blocks,
        html=html,
        page_jpeg=page_jpeg,
        extra_meta={"reprocess_mode": mode, **(extra_meta or {})},
    )

    upsert_page_draft_chunk(
        session,
        job,
        page_no=page_no,
        page_content=html,
        total_pages=total_pages,
        engine=engine,
        content_format="html",
        durable=False,
    )
    if durable:
        _commit_durable(session, what=f"page_html+ir_meta p{page_no}", job_id=job.id, job=job)
    else:
        session.flush()

    fig_with_url = sum(
        1 for b in page_blocks if b.type == "figure" and (b.asset_url or b.asset_key)
    )
    return {
        "ok": True,
        "job_id": job.id,
        "page_no": page_no,
        "mode": mode,
        "html": html,
        "figure_count": sum(1 for b in page_blocks if b.type == "figure"),
        "figure_with_asset": fig_with_url,
        "finding_count": debug_meta.get("finding_count", 0),
        "findings": debug_meta.get("findings") or [],
        "persisted": True,
        **(extra_meta or {}),
    }


def _patch_preview(refine_result_dump: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": p.get("id"),
            "zone": p.get("zone"),
            "role": p.get("pedagogy_role"),
            "text": str(p.get("text") or "")[:80],
        }
        for p in (refine_result_dump.get("blocks") or [])[:12]
    ]


def reprocess_page(
    session: Session,
    job: CorpusImportJob,
    page_no: int,
    *,
    mode: ReprocessMode | str = "layout_content",
    user_note: str = "",
) -> dict[str, Any]:
    """Reprocess one page. Default: layout_content (35B patch on existing IR)."""
    mode = normalize_reprocess_mode(str(mode))
    meta = loads(job.meta_json, {})
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    engine = str(meta.get("parse_engine") or "mineru_cloud")
    total_pages = int(meta.get("total_pages") or meta.get("ocr_total") or 0)

    blocks = load_blocks(job) or []
    rebuilt_from_cloud = False
    restored_mineru = False
    refine_patches = 0
    refine_result_dump: dict[str, Any] = {}
    before_page: list[dict[str, Any]] = []
    effective_changes = 0
    judge_summary: dict[str, Any] = {}
    working_before = [b for b in blocks if b.page_no == page_no]

    if mode in ("from_cloud", "restore_mineru"):
        baseline = load_mineru_baseline(job, page_no)
        cloud_page: list[DocumentBlock] | None = None
        if mode == "restore_mineru" and baseline:
            # Prefer frozen MinerU snapshot; never replace baseline here.
            cloud_page = baseline
            restored_mineru = True
        else:
            try:
                cloud_page = _rebuild_page_from_cloud(job, page_no, engine=engine)
            except Exception as exc:
                logger.warning(
                    "cloud rebuild failed job=%s page=%s: %s", job.id, page_no, exc
                )
                cloud_page = None
            if cloud_page is not None:
                rebuilt_from_cloud = True
                # Seed baseline once; never clobber an existing MinerU snapshot.
                save_mineru_baseline(job, page_no, cloud_page, overwrite=False)
                if mode == "restore_mineru":
                    restored_mineru = True
        if cloud_page is not None:
            others = [b for b in blocks if b.page_no != page_no]
            blocks = others + cloud_page
            blocks.sort(key=lambda b: (b.page_no, b.order))
            _save_blocks(session, job, blocks)
        elif mode == "restore_mineru":
            raise ValueError(
                f"no MinerU baseline for page {page_no} "
                "(and Cloud content_list slice unavailable)"
            )

    page_blocks = [b for b in blocks if b.page_no == page_no]
    if not page_blocks:
        raise ValueError(
            f"page {page_no} not found in stored IR "
            f"(run book import first; single-page 35B does not call MinerU)"
        )

    if not total_pages:
        total_pages = max((b.page_no for b in blocks), default=page_no)

    profile = load_textbook_profile(profile_id)
    pdf_bytes = get_object_bytes(job.minio_key) if job.minio_key else b""
    page_w, page_h = 596.0, 843.0
    cache: PdfRenderCache | None = None
    page_jpeg: bytes | None = None
    if pdf_bytes:
        cache = PdfRenderCache(pdf_bytes)
        try:
            page_w, page_h = cache.page_size(page_no)
        except Exception:
            pass
        try:
            page_jpeg = cache.page_jpeg(page_no, dpi=150)
        except Exception as exc:
            logger.warning("page jpeg 150dpi failed job=%s page=%s: %s", job.id, page_no, exc)

    try:
        if mode in _REFINE_MODES:
            # Freeze MinerU rollback point before any 35B mutation.
            ensure_mineru_baseline(job, page_no, page_blocks)

        if mode in ("content_correct", "layout_content"):
            if not pdf_bytes and not page_jpeg:
                raise ValueError("PDF/page image required for 35B re-recognize")
            before_page = [b.model_dump(mode="json") for b in page_blocks]
            before_objs = list(page_blocks)
            refine_mode = "content_correct" if mode == "content_correct" else "rerecognize"
            page_result = refine_page(
                pdf_bytes or b"",
                page_no,
                page_blocks,
                profile_id=profile_id,
                mode=refine_mode,
                render_cache=cache,
                jpeg_bytes=page_jpeg,
                user_note=user_note,
            )
            refine_patches = len(page_result.blocks)
            refine_result_dump = page_result.model_dump(mode="json")
            page_blocks = apply_page_refine(
                page_blocks,
                page_result,
                content_only=(mode == "content_correct"),
            )
            page_blocks = enrich_page_blocks_math(page_blocks)
            if mode == "content_correct":
                pass
            else:
                page_blocks = enrich_page_blocks_slim(
                    page_blocks, profile=profile, page_w=page_w, page_h=page_h
                )
            effective_changes = count_effective_changes(before_objs, page_blocks)
        elif mode == "layout_rebuild":
            if not page_jpeg:
                raise ValueError("PDF/page image required for layout_rebuild")
            from app.services.ir.page_fusion import run_layout_rebuild

            before_page = [b.model_dump(mode="json") for b in page_blocks]
            before_objs = list(page_blocks)
            fused, dump = run_layout_rebuild(
                page_no=page_no,
                page_blocks=page_blocks,
                jpeg=page_jpeg,
                user_note=user_note,
                edition_id=str(
                    (profile or {}).get("edition_id")
                    or (profile or {}).get("profile_id")
                    or ""
                )
                or None,
            )
            refine_result_dump = dump
            refine_patches = len(fused)
            judge_summary = dict(dump.get("judge_summary") or {})
            page_blocks = enrich_page_blocks_math(fused)
            page_blocks = enrich_page_blocks_slim(
                page_blocks, profile=profile, page_w=page_w, page_h=page_h
            )
            effective_changes = count_effective_changes(before_objs, page_blocks)
        else:
            # restore_mineru / from_cloud / rerender / from_blocks
            page_blocks = enrich_page_blocks_math(page_blocks)
            page_blocks = enrich_page_blocks_slim(
                page_blocks, profile=profile, page_w=page_w, page_h=page_h
            )
            if mode in ("restore_mineru", "from_cloud") and working_before:
                effective_changes = count_effective_changes(
                    working_before, page_blocks
                )

        others = [b for b in blocks if b.page_no != page_no]
        blocks = others + page_blocks
        blocks.sort(key=lambda b: (b.page_no, b.order))
        _save_blocks(session, job, blocks, durable=True)
    except Exception:
        if cache is not None:
            cache.close()
        raise

    try:
        used_35b = mode in _REFINE_MODES
        out = _finalize_and_persist(
            session,
            job,
            blocks=blocks,
            page_no=page_no,
            page_blocks=page_blocks,
            profile_id=profile_id,
            engine=engine,
            total_pages=total_pages,
            mode=mode,
            extra_meta={
                "rebuilt_from_cloud": rebuilt_from_cloud,
                "restored_mineru_baseline": restored_mineru,
                **baseline_meta_stub(job, page_no),
                "refine_patches": refine_patches,
                "effective_changes": effective_changes,
                "noop": used_35b and effective_changes == 0,
                "judge_summary": judge_summary,
                "used_35b": used_35b,
                "used_mineru": rebuilt_from_cloud or restored_mineru,
                "user_note": (user_note or "")[:500],
                "refine_patch_ids": [
                    str(p.get("id") or "")
                    for p in (refine_result_dump.get("blocks") or [])[:40]
                ],
                "refine_patch_preview": _patch_preview(refine_result_dump),
            },
            page_jpeg=page_jpeg,
            cache=cache,
            durable=True,
        )
        if mode in _REFINE_MODES:
            from app.services import improvement_points as ip_svc
            from app.services.corpus_corrections import save_correction

            summary = (user_note or "").strip() or f"reprocess refine page {page_no}"
            after_blocks = [b.model_dump(mode="json") for b in page_blocks]
            ip_out = ip_svc.confirm_improvement(
                session,
                job_id=job.id,
                fingerprint_hash="",
                page_no=page_no,
                summary=summary,
                authority="edition",
                kind="reprocess_refine",
                attempts=[
                    {
                        "id": f"reprocess-p{page_no}",
                        "page": page_no,
                        "tool": "corpus.reprocess_page",
                        "op": "reprocess_page",
                        "summary": summary[:300],
                        "args_summary": summary[:300],
                    }
                ],
                procedure={
                    "steps": [
                        {
                            "tool": "corpus.reprocess_page",
                            "args_summary": summary[:300],
                        }
                    ],
                    "verify": "user_confirm",
                    "avoid": [],
                },
                concrete={
                    "scope": "page",
                    "page_no": page_no,
                    "block_id": "",
                    "before": "",
                    "after": summary[:500],
                },
                write_correction=False,
            )
            ip_id = str((ip_out.get("ip") or {}).get("id") or "")
            corr = save_correction(
                session,
                job=job,
                blocks=blocks,
                scope="page",
                page_no=page_no,
                block_id="",
                user_note=user_note,
                before_blocks=before_page,
                after_blocks=after_blocks,
                refine_result=refine_result_dump,
                ip_id=ip_id,
                procedure=ip_out.get("ip", {}).get("procedure")
                if isinstance(ip_out.get("ip"), dict)
                else None,
            )
            _commit_durable(
                session,
                what=f"correction page p{page_no}",
                job_id=job.id,
                job=job,
            )
            out["correction_id"] = corr.id
            out["ip_id"] = ip_id
            out["fingerprint_hash"] = corr.fingerprint_hash
            out["persisted"] = True
    finally:
        if cache is not None:
            cache.close()
    return out


def refine_block(
    session: Session,
    job: CorpusImportJob,
    page_no: int,
    block_id: str,
    *,
    user_note: str = "",
) -> dict[str, Any]:
    """Re-recognize one block: last block IR + page image → 35B (no MinerU)."""
    meta = loads(job.meta_json, {})
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    engine = str(meta.get("parse_engine") or "mineru_cloud")
    total_pages = int(meta.get("total_pages") or meta.get("ocr_total") or 0)

    blocks = load_blocks(job) or []
    page_blocks = [b for b in blocks if b.page_no == page_no]
    if not page_blocks:
        raise ValueError(f"page {page_no} not found in stored IR")
    if not any(b.id == block_id for b in page_blocks):
        raise ValueError(f"block {block_id} not found on page {page_no}")

    if not total_pages:
        total_pages = max((b.page_no for b in blocks), default=page_no)

    profile = load_textbook_profile(profile_id)
    pdf_bytes = get_object_bytes(job.minio_key) if job.minio_key else b""
    if not pdf_bytes:
        raise ValueError("PDF required for block 35B re-recognize")

    before_focus = [
        b.model_dump(mode="json") for b in page_blocks if b.id == block_id
    ]
    before_objs = list(page_blocks)
    ensure_mineru_baseline(job, page_no, page_blocks)
    cache = PdfRenderCache(pdf_bytes)
    try:
        page_w, page_h = cache.page_size(page_no)
        page_jpeg = cache.page_jpeg(page_no, dpi=150)
        page_result = refine_one_block(
            pdf_bytes,
            page_no,
            blocks,
            block_id,
            profile_id=profile_id,
            render_cache=cache,
            jpeg_bytes=page_jpeg,
            user_note=user_note,
        )
        page_blocks = apply_page_refine(page_blocks, page_result)
        page_blocks = enrich_page_blocks_math(page_blocks)
        page_blocks = enrich_page_blocks_slim(
            page_blocks, profile=profile, page_w=page_w, page_h=page_h
        )
        effective_changes = count_effective_changes(before_objs, page_blocks)
        others = [b for b in blocks if b.page_no != page_no]
        blocks = others + page_blocks
        blocks.sort(key=lambda b: (b.page_no, b.order))
        _save_blocks(session, job, blocks, durable=True)

        out = _finalize_and_persist(
            session,
            job,
            blocks=blocks,
            page_no=page_no,
            page_blocks=page_blocks,
            profile_id=profile_id,
            engine=engine,
            total_pages=total_pages,
            mode="refine_block_35b",
            extra_meta={
                "block_id": block_id,
                "refine_patches": len(page_result.blocks),
                "effective_changes": effective_changes,
                "noop": effective_changes == 0,
                "used_35b": True,
                "used_mineru": False,
                "user_note": (user_note or "")[:500],
                "refine_patch_ids": [p.id for p in page_result.blocks][:20],
                "refine_patch_preview": [
                    {
                        "id": p.id,
                        "zone": p.zone,
                        "role": p.pedagogy_role,
                        "text": str(p.text or "")[:80],
                    }
                    for p in page_result.blocks[:8]
                ],
            },
            page_jpeg=page_jpeg,
            cache=cache,
            durable=True,
        )
        from app.services import improvement_points as ip_svc
        from app.services.corpus_corrections import save_correction

        after_focus = [
            b.model_dump(mode="json") for b in page_blocks if b.id == block_id
        ]
        summary = (user_note or "").strip() or f"reprocess refine block {block_id}"
        ip_out = ip_svc.confirm_improvement(
            session,
            job_id=job.id,
            fingerprint_hash="",
            page_no=page_no,
            summary=summary,
            authority="edition",
            kind="reprocess_refine_block",
            block_ids=[block_id] if block_id else [],
            attempts=[
                {
                    "id": f"reprocess-b-{block_id or page_no}",
                    "page": page_no,
                    "tool": "corpus.reprocess_block",
                    "op": "reprocess_block",
                    "summary": summary[:300],
                    "args_summary": summary[:300],
                    "block_ids": [block_id] if block_id else [],
                }
            ],
            procedure={
                "steps": [
                    {
                        "tool": "corpus.reprocess_block",
                        "args_summary": summary[:300],
                    }
                ],
                "verify": "user_confirm",
                "avoid": [],
            },
            write_correction=False,
        )
        ip_id = str((ip_out.get("ip") or {}).get("id") or "")
        corr = save_correction(
            session,
            job=job,
            blocks=blocks,
            scope="block",
            page_no=page_no,
            block_id=block_id,
            user_note=user_note,
            before_blocks=before_focus,
            after_blocks=after_focus or [
                b.model_dump(mode="json") for b in page_blocks
            ],
            refine_result=page_result.model_dump(mode="json"),
            ip_id=ip_id,
            procedure=ip_out.get("ip", {}).get("procedure")
            if isinstance(ip_out.get("ip"), dict)
            else None,
        )
        _commit_durable(
            session,
            what=f"correction block {block_id}",
            job_id=job.id,
            job=job,
        )
        out["correction_id"] = corr.id
        out["ip_id"] = ip_id
        out["fingerprint_hash"] = corr.fingerprint_hash
    finally:
        cache.close()
    return out


def get_rerecognize_context(
    session: Session,
    job: CorpusImportJob,
    page_no: int,
    *,
    block_id: str = "",
) -> dict[str, Any]:
    """Structured stage snapshot for the Studio re-recognize dialog."""
    from app.services.corpus_corrections import extract_book_features
    from app.services.corpus_debug_bundle import (
        load_page_debug_artifact,
        load_page_debug_index,
        slice_cloud_page_blocks,
    )
    from app.services.ir.mineru_page_refine import blocks_to_refine_payload

    del session  # reserved
    meta = loads(job.meta_json, {})
    blocks = load_blocks(job) or []
    page_blocks = [b for b in blocks if b.page_no == page_no]
    if not page_blocks:
        raise ValueError(f"page {page_no} not found in stored IR")

    focus = page_blocks
    if block_id:
        focus = [b for b in page_blocks if b.id == block_id]
        if not focus:
            raise ValueError(f"block {block_id} not found on page {page_no}")

    stages: dict[str, Any] = {}
    try:
        stages["debug_index"] = load_page_debug_index(job, page_no)
    except Exception as exc:
        stages["debug_index"] = {"error": str(exc)[:200]}

    for art in ("meta", "mineru_page", "blocks"):
        try:
            stages[art] = load_page_debug_artifact(job, page_no, art)
        except Exception as exc:
            stages[art] = {"error": str(exc)[:200]}

    cloud = slice_cloud_page_blocks(job, page_no)
    if cloud is not None:
        stages["mineru_cloud_slice"] = cloud[:80]

    feats = extract_book_features(job, blocks)
    return {
        "job_id": job.id,
        "page_no": page_no,
        "block_id": block_id or "",
        "scope": "block" if block_id else "page",
        "label": job.label,
        "profile": meta.get("textbook_profile"),
        "parse_engine": meta.get("parse_engine"),
        "fingerprint_preview": {
            "hash": feats["fingerprint_hash"],
            "page_count": feats["page_count"],
            "toc_titles": (feats.get("toc_titles") or [])[:12],
            "toc_hash": feats["toc_hash"],
            "first_page_hash": feats["first_page_hash"],
        },
        "ir_blocks": [b.model_dump(mode="json") for b in focus],
        "refine_payload": blocks_to_refine_payload(
            page_blocks if not block_id else focus,
            page_no,
            full_text=True,
        ),
        "stages": stages,
        "prior_fingerprint": meta.get("book_fingerprint"),
    }
