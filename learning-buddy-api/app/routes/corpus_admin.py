from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.services.import_assets import assert_allowed_asset_key

from app.clients.redis_client import enqueue_import_job, enqueue_scout_run
from app.db.models import CorpusImportJob
from app.db.session import db_session
from app.schemas.corpus import (
    BulkPublishRequest,
    CorpusEdgeCreate,
    CorpusNodeCreate,
    CorpusNodeOut,
    CorpusNodePatch,
    CorpusStatsOut,
    GraphOut,
    AdoptFingerprintRequest,
    BookEditionBindRequest,
    CheckDryRunRequest,
    CheckExamplesRequest,
    CheckFalsePositiveRequest,
    FinalizeBookRequest,
    ImportJobOut,
    ImportPdfRequest,
    InboxItemOut,
    InheritPagesRequest,
    IpAttemptIngestRequest,
    IpConfirmRequest,
    IpConvergeRequest,
    IpPatternRequest,
    IpPrepConflictResolveRequest,
    IpResolveConflictRequest,
    IpSplitApplyRequest,
    IpSplitProposeRequest,
    IpTriageApplyRequest,
    MarkdownPatch,
    NavContextOut,
    NavCrumb,
    NodeLinksOut,
    PatternBindDetectorRequest,
    PatternLandRequest,
    PatternMergeRequest,
    PatternRouteRequest,
    PatternSplitRequest,
    PromoteQuestionRequest,
    PublishSubtreeOut,
    ReaderPageOut,
    RebuildIrRequest,
    ReindexAsyncOut,
    ReindexJobStatusOut,
    ReindexRequest,
    ScoutRequest,
    ScoutRunOut,
    SearchPreviewRequest,
    SetStatusRequest,
    TreeNodeOut,
    WorkQueueOut,
)
from app.services import corpus_admin as svc

logger = logging.getLogger(__name__)
from app.services.corpus_import import process_import_job, run_import_chunk
from app.services.corpus_governance import PublishGuardError
from app.services.corpus_locale import LocaleValidationError
from app.services.corpus_publish import embed_staging, publish_node
from app.services.scout_agent import approve_inbox_item
from app.util.json_util import loads

router = APIRouter(prefix="/api/v1/admin/corpus", tags=["corpus-admin"])


@router.get("/stats", response_model=CorpusStatsOut)
def corpus_stats(session: Session = Depends(db_session)) -> CorpusStatsOut:
    return svc.get_stats(session)


@router.get("/tree", response_model=list[TreeNodeOut])
def corpus_tree(
    root: str = Query(..., description="textbook|kp|method|shortcut|question|import|inbox"),
    parent_id: str = "",
    subject: str | None = None,
    grade_band: str | None = None,
    depth: int = 1,
    session: Session = Depends(db_session),
) -> list[TreeNodeOut]:
    return svc.get_corpus_tree(
        session,
        root=root,
        parent_id=parent_id,
        subject=subject,
        grade_band=grade_band,
        depth=depth,
    )


@router.get("/nodes/search", response_model=list[CorpusNodeOut])
def search_nodes(
    q: str = "",
    subject: str | None = None,
    node_type: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=200),
    session: Session = Depends(db_session),
) -> list[CorpusNodeOut]:
    return svc.search_nodes(
        session, q=q, subject=subject, node_type=node_type, status=status, limit=limit
    )


@router.get("/nodes/{node_id}/reader-pages", response_model=list[ReaderPageOut])
def textbook_reader_pages(
    node_id: str,
    session: Session = Depends(db_session),
) -> list[ReaderPageOut]:
    """PDF pages for continuous scroll reader (textbook center pane)."""
    from app.services.corpus_tree import list_textbook_reader_pages

    return list_textbook_reader_pages(session, node_id)


@router.get("/nodes/{node_id}/links", response_model=NodeLinksOut)
def node_links(
    node_id: str,
    hide: str | None = Query(None, description="Comma-separated rel_types to hide"),
    session: Session = Depends(db_session),
) -> NodeLinksOut:
    return svc.get_node_links(session, node_id, hide=hide)


@router.get("/workqueue", response_model=WorkQueueOut)
def workqueue(
    limit: int = Query(20, le=100),
    import_job_id: str | None = Query(None),
    session: Session = Depends(db_session),
) -> WorkQueueOut:
    return svc.get_workqueue(session, limit=limit, import_job_id=import_job_id)


@router.post("/nodes/bulk-publish")
def bulk_publish(body: BulkPublishRequest, session: Session = Depends(db_session)) -> dict:
    return svc.bulk_publish(session, body)


@router.post("/nodes/{node_id}/publish-subtree", response_model=PublishSubtreeOut)
def publish_subtree(node_id: str, session: Session = Depends(db_session)) -> PublishSubtreeOut:
    return svc.publish_subtree(session, node_id)


@router.post("/nodes/{node_id}/set-status", response_model=CorpusNodeOut)
def set_status(
    node_id: str,
    body: SetStatusRequest,
    session: Session = Depends(db_session),
) -> CorpusNodeOut:
    try:
        out = svc.set_node_status(session, node_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if out is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return out


@router.get("/nodes", response_model=list[CorpusNodeOut])
def list_nodes(
    status: str | None = None,
    node_type: str | None = None,
    subject: str | None = None,
    grade_band: str | None = None,
    limit: int = Query(100, le=500),
    session: Session = Depends(db_session),
) -> list[CorpusNodeOut]:
    return svc.list_nodes(
        session,
        status=status,
        node_type=node_type,
        subject=subject,
        grade_band=grade_band,
        limit=limit,
    )


@router.get("/nodes/{node_id}", response_model=CorpusNodeOut)
def get_node(node_id: str, session: Session = Depends(db_session)) -> CorpusNodeOut:
    out = svc.get_node(session, node_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return out


@router.get("/nodes/{node_id}/nav-context", response_model=NavContextOut)
def get_nav_context(node_id: str, session: Session = Depends(db_session)) -> NavContextOut:
    """Breadcrumb + first page chunk in one call (avoids Studio N+1 getNode walks)."""
    node = svc.get_node(session, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    crumbs: list[NavCrumb] = []
    links = node.links or {}
    if links.get("textbook_id"):
        tb = svc.get_node(session, str(links["textbook_id"]))
        if tb:
            crumbs.append(NavCrumb(id=tb.id, label=tb.label))
    if links.get("section_id"):
        sec = svc.get_node(session, str(links["section_id"]))
        if sec:
            crumbs.append(NavCrumb(id=sec.id, label=sec.label))
    crumbs.append(NavCrumb(id=node.id, label=node.label))

    first_page: str | None = None
    textbook_id = str(links.get("textbook_id") or (node.id if node.node_type == "textbook" else ""))
    if textbook_id:
        try:
            sections = svc.get_corpus_tree(session, root="textbook", parent_id=textbook_id)
            for sec in sections:
                chunks = svc.get_corpus_tree(session, root="textbook", parent_id=sec.id)
                for c in chunks:
                    if c.node_type != "chunk" or getattr(c, "status", None) != "draft":
                        continue
                    meta = getattr(c, "meta", None) or {}
                    kind = str(meta.get("chunk_kind") or "")
                    page = int(meta.get("source_page") or 0)
                    if kind == "page_import" or page > 0 or (c.id and ":p" in c.id):
                        first_page = c.id
                        break
                if first_page:
                    break
        except Exception:
            first_page = None

    return NavContextOut(node=node, breadcrumb=crumbs, first_page_chunk_id=first_page)


@router.post("/nodes", response_model=CorpusNodeOut)
def create_node(body: CorpusNodeCreate, session: Session = Depends(db_session)) -> CorpusNodeOut:
    return svc.create_node(session, body)


@router.patch("/nodes/{node_id}", response_model=CorpusNodeOut)
def patch_node(
    node_id: str,
    body: CorpusNodePatch,
    session: Session = Depends(db_session),
) -> CorpusNodeOut:
    try:
        out = svc.patch_node(session, node_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if out is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return out


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: str,
    cascade: bool = Query(False, description="Delete entire subtree"),
    session: Session = Depends(db_session),
) -> dict:
    try:
        out = svc.delete_node(session, node_id, cascade=cascade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if out["count"] == 0:
        raise HTTPException(status_code=404, detail="Node not found")
    return out


@router.get("/nodes/{node_id}/backlinks", response_model=list[CorpusNodeOut])
def backlinks(node_id: str, session: Session = Depends(db_session)) -> list[CorpusNodeOut]:
    return svc.get_backlinks(session, node_id)


@router.post("/nodes/{node_id}/edges")
def add_edge(node_id: str, body: CorpusEdgeCreate, session: Session = Depends(db_session)) -> dict:
    svc.add_node_edge(session, node_id, body)
    return {"from_id": node_id, "to_id": body.to_id, "rel_type": body.rel_type}


@router.post("/nodes/{node_id}/publish")
def publish(node_id: str, session: Session = Depends(db_session)) -> dict:
    try:
        n = publish_node(session, node_id)
    except LocaleValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PublishGuardError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"published": node_id, "vectors": n}


@router.post("/nodes/{node_id}/submit-review", response_model=CorpusNodeOut)
def submit_review(node_id: str, session: Session = Depends(db_session)) -> CorpusNodeOut:
    try:
        out = svc.submit_node_for_review(session, node_id)
    except LocaleValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if out is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return out


@router.post("/reset-workspace")
def reset_workspace(
    reseed: bool = Query(False, description="Reload seed data from data/ into PG as draft (not Milvus)"),
    session: Session = Depends(db_session),
) -> dict:
    """清空 Milvus + PG 语料图；默认完全空库。reseed=true 仅重载 PG draft，仍须审核发布才进向量库。"""
    return svc.reset_corpus_workspace_admin(session, reseed=reseed)


@router.post("/nodes/{node_id}/embed-staging")
def embed_staging_route(node_id: str, session: Session = Depends(db_session)) -> dict:
    try:
        n = embed_staging(session, node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"node_id": node_id, "vectors": n, "collection": "staging"}


@router.post("/search-preview")
def search_preview(body: SearchPreviewRequest) -> dict:
    return svc.search_preview(body)


@router.post("/reindex")
def reindex(body: ReindexRequest, session: Session = Depends(db_session)) -> dict:
    return svc.reindex_corpus(session, body)


@router.post("/reindex/async", response_model=ReindexAsyncOut)
def reindex_async(body: ReindexRequest, session: Session = Depends(db_session)) -> ReindexAsyncOut:
    try:
        return svc.start_reindex_async(session, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/reindex/jobs/{job_id}", response_model=ReindexJobStatusOut)
def reindex_job_status(job_id: str) -> ReindexJobStatusOut:
    out = svc.get_reindex_job_status(job_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return out


@router.post("/import/pdf", response_model=ImportJobOut)
def import_pdf(body: ImportPdfRequest, session: Session = Depends(db_session)) -> ImportJobOut:
    job_out = svc.import_pdf_start(session, body)
    # Commit before Redis enqueue so buddy-worker always sees the row.
    session.commit()
    enqueue_import_job(job_out.id)
    return job_out


@router.post("/import/pdf-probe")
async def probe_pdf(request: Request, session: Session = Depends(db_session)) -> dict:
    """Read basic PDF metadata without creating a job or uploading to MinIO.

    Accepts raw PDF bytes in the request body (application/pdf) to avoid a
    multipart dependency on buddy-api. The Studio gateway forwards bytes here.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    # 网关侧 percent 编码（HTTP 头仅允许 latin-1，中文文件名），这里解码；纯 ASCII 名 unquote 无副作用
    from urllib.parse import unquote

    filename = unquote(request.headers.get("x-filename", "upload.pdf"))
    from app.services.pdf_extract import extract_pdf_bytes

    try:
        result = extract_pdf_bytes(data, filename=filename)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF 解析失败：{exc}") from exc
    out = {
        "page_count": result.meta["page_count"],
        "pdf_kind": result.meta["pdf_kind"],
        "avg_chars_per_page": result.meta["avg_chars_per_page"],
        "file_size_bytes": len(data),
        "filename": filename,
    }
    # Recognise a re-import here, before any cloud parse is spent, so the panel
    # can offer "regenerate" vs "inherit last result".
    try:
        from app.services.book_identity import probe_known_books

        out["known_books"] = probe_known_books(session, data, filename=filename)
        session.commit()
    except Exception:  # noqa: BLE001 - identity is advisory, never blocks probe
        session.rollback()
        logger.warning("known-book probe failed filename=%s", filename, exc_info=True)
        out["known_books"] = {"candidates": [], "best": None}
    return out


@router.get("/import/jobs", response_model=list[ImportJobOut])
def list_import_jobs(session: Session = Depends(db_session)) -> list[ImportJobOut]:
    return svc.list_import_jobs(session)


def _asset_content_type(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")


@router.get("/import/asset")
def get_import_asset(key: str = Query(..., min_length=1)) -> Response:
    """Serve import figure bytes from MinIO (Studio proxy forwards here)."""
    try:
        assert_allowed_asset_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    data = get_object_bytes(key)
    if not data:
        raise HTTPException(status_code=404, detail="asset not found")
    return Response(content=data, media_type=_asset_content_type(key))


@router.get("/import/textbook.css")
def get_textbook_css(edition_id: str | None = Query(default=None)) -> Response:
    from app.services.editions.bundle import SEED_HUJIAO, theme_css_path

    eid = (edition_id or "").strip() or SEED_HUJIAO
    try:
        css_path = theme_css_path(eid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not css_path.is_file():
        raise HTTPException(status_code=404, detail="stylesheet not found")
    return Response(
        content=css_path.read_text(encoding="utf-8"),
        media_type="text/css",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/import/jobs/{job_id}/blocks")
def get_import_blocks(job_id: str, session: Session = Depends(db_session)) -> dict:
    from app.services.import_blocks_service import get_job_blocks

    try:
        job, blocks = get_job_blocks(session, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job.id, "block_count": len(blocks), "blocks": blocks}


@router.patch("/import/jobs/{job_id}/blocks/{block_id}")
def patch_import_block(
    job_id: str,
    block_id: str,
    body: dict,
    session: Session = Depends(db_session),
) -> dict:
    from app.services.import_blocks_service import patch_job_block

    try:
        job, blocks, info = patch_job_block(session, job_id, block_id, body)
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job.id, "block_count": len(blocks), "blocks": blocks, **info}


@router.put("/import/jobs/{job_id}/pages/{page_no}/blocks")
def put_import_page_blocks(
    job_id: str,
    page_no: int,
    body: dict,
    session: Session = Depends(db_session),
) -> dict:
    """Replace/reorder IR blocks on one page (Cursor layout rewrite, no 35B)."""
    from app.services.import_blocks_service import replace_page_blocks

    raw = body.get("blocks") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="body.blocks must be a list")
    rerender = True
    if isinstance(body, dict) and "rerender" in body:
        rerender = bool(body.get("rerender"))
    try:
        job, page_blocks, html, info = replace_page_blocks(
            session, job_id, page_no, raw, rerender=rerender
        )
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": job.id,
        "page_no": page_no,
        "block_count": len(page_blocks),
        "blocks": page_blocks,
        "html": html,
        "rerendered": html is not None,
        **info,
    }


@router.post("/import/jobs/{job_id}/rerender-page/{page_no}")
def rerender_import_page(
    job_id: str,
    page_no: int,
    session: Session = Depends(db_session),
) -> dict:
    from app.services.import_blocks_service import rerender_import_page

    try:
        html = rerender_import_page(session, job_id, page_no)
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "page_no": page_no, "html": html}


@router.get("/import/jobs/{job_id}/page/{page_no}/html")
def get_import_page_html(
    job_id: str,
    page_no: int,
    session: Session = Depends(db_session),
) -> dict:
    """Return page HTML for Studio preview.

    Prefer the draft page chunk written at import time (fast). Fall back to
    live IR re-render only when the chunk is missing — re-rendering every
    page from full IR on preview open is too slow for whole-book jobs.
    """
    from app.db.models import CorpusNode
    from app.services.corpus_import_incremental import page_chunk_id, textbook_id_for_job
    from app.services.import_ir_pipeline import get_job_ir, rerender_page_html
    from app.services.ir.textbook_profile import default_profile_id

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    meta = loads(job.meta_json, {})
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    total_pages = int(meta.get("total_pages") or meta.get("ocr_total") or 0)

    tb_id = job.textbook_node_id or textbook_id_for_job(job)
    chunk_id = page_chunk_id(tb_id, page_no)
    node = session.get(CorpusNode, chunk_id)
    if node is not None and (node.full_text or "").strip():
        from app.services.document_ir import polish_page_html

        return {
            "job_id": job_id,
            "page_no": page_no,
            "html": polish_page_html(node.full_text),
            "source": "draft_chunk",
            "total_pages": total_pages,
        }

    blocks = get_job_ir(job)
    if blocks:
        page_blocks = [b for b in blocks if b.page_no == page_no]
        if page_blocks:
            html = rerender_page_html(blocks, page_no, profile_id=profile_id)
            return {
                "job_id": job_id,
                "page_no": page_no,
                "html": html,
                "source": "ir",
                "total_pages": total_pages,
            }

    raise HTTPException(status_code=404, detail=f"page {page_no} not ready")


@router.post("/import/jobs/{job_id}/rebuild-ir")
def rebuild_import_ir(
    job_id: str,
    body: RebuildIrRequest | None = None,
    session: Session = Depends(db_session),
) -> dict:
    from app.services.corpus_import_incremental import _job_meta
    from app.services.import_ir_pipeline import rebuild_job_ir

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    pages = body.pages if body else None
    # 规则是全书生效的，落地前后的 escalate 页数就是回归闸：只看改动页会漏掉被弄坏的页。
    before_escalate = sorted(int(p) for p in (_job_meta(job).get("layout_escalate_pages") or []))
    try:
        _blocks, refine_meta = rebuild_job_ir(session, job, pages=pages)
        from app.services.corpus_import_incremental import run_import_structure_merge

        job = run_import_structure_merge(session, job_id)
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    after_escalate = sorted(int(p) for p in (_job_meta(job).get("layout_escalate_pages") or []))
    return {
        "job_id": job_id,
        "refine_prompt_version": refine_meta.get("refine_prompt_version"),
        "page_refine_chunks": len(refine_meta.get("page_refine_chunks") or []),
        "refine_errors": len(refine_meta.get("refine_errors") or []),
        "rebuilt_pages": pages or "all",
        "escalate": {
            "before": len(before_escalate),
            "after": len(after_escalate),
            "delta": len(after_escalate) - len(before_escalate),
            "newly_escalated": sorted(set(after_escalate) - set(before_escalate))[:50],
            "resolved": sorted(set(before_escalate) - set(after_escalate))[:50],
        },
    }


# --- Same-book re-import: adopt a prior identity / inherit finished pages ---


@router.get("/books/fingerprints")
def list_book_fingerprints(
    subject: str = "",
    grade_band: str = "",
    session: Session = Depends(db_session),
) -> dict:
    """Book identities with ledger health — the picker behind 认亲."""
    from app.db.models import CorpusBookFingerprint
    from app.services.corpus_corrections import correction_ledger_stats

    q = session.query(CorpusBookFingerprint)
    if subject:
        q = q.filter(CorpusBookFingerprint.subject == subject)
    if grade_band:
        q = q.filter(CorpusBookFingerprint.grade_band == grade_band)
    items = []
    for row in q.all():
        stats = correction_ledger_stats(session, row.fingerprint_hash)
        if not stats["corrections"] and not stats["accepted_ips"]:
            continue
        items.append(
            {
                "fingerprint_hash": row.fingerprint_hash,
                "subject": row.subject,
                "grade_band": row.grade_band,
                "page_count": row.page_count,
                "label_hint": row.label_hint,
                "updated_at": row.updated_at,
                **{k: v for k, v in stats.items() if k != "fingerprint_hash"},
            }
        )
    items.sort(key=lambda x: (-int(x["accepted_ips"]), -int(x["replayable"])))
    return {"items": items, "total": len(items)}


@router.post("/import/jobs/{job_id}/adopt-fingerprint")
def adopt_import_fingerprint(
    job_id: str,
    body: AdoptFingerprintRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services.corpus_corrections import (
        adopt_book_fingerprint,
        apply_matched_corrections,
    )

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        out = adopt_book_fingerprint(
            session, job, body.fingerprint_hash, note=body.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.replay:
        try:
            out["replay"] = apply_matched_corrections(session, job)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return out


@router.post("/import/jobs/{job_id}/inherit-pages")
def inherit_import_pages(
    job_id: str,
    body: InheritPagesRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services.corpus_page_inherit import inherit_pages_from_job

    try:
        return inherit_pages_from_job(
            session,
            old_job_id=body.from_job_id,
            new_job_id=job_id,
            pages=body.pages,
            only_improved=body.only_improved,
            dry_run=body.dry_run,
            max_offset=body.max_offset,
            include_low_confidence=body.include_low_confidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- MinerU ops: page reprocess / debug / feedback / cursor pack ------------


@router.post("/import/jobs/{job_id}/pages/{page_no}/reprocess")
def reprocess_import_page(
    job_id: str,
    page_no: int,
    body: dict | None = None,
    session: Session = Depends(db_session),
) -> dict:
    """Reprocess one page.

    Modes:
      - ``restore_mineru`` / ``mineru_rerecognize``: restore page IR from MinerU
        baseline (seeded once; never overwritten by 35B)
      - ``content_correct``: text/latex only (topology frozen)
      - ``layout_content`` / legacy ``refine_35b``: patch text+zone+role
      - ``layout_rebuild``: rebuild + align + judge + fuse (rollback via restore_mineru)
      - ``rerender`` / ``from_blocks``: HTML from stored IR
      - ``from_cloud``: ops — re-adapt from persisted MinerU content_list
    Optional ``user_note``: improvement hint for 35B (also persisted as correction).
    """
    from app.services.corpus_reprocess_service import (
        normalize_reprocess_mode,
        reprocess_page,
    )

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    mode = "layout_content"
    user_note = ""
    if isinstance(body, dict):
        if body.get("mode"):
            mode = str(body["mode"])
        user_note = str(body.get("user_note") or body.get("note") or "")
    allowed = {
        "restore_mineru",
        "mineru_rerecognize",
        "content_correct",
        "layout_content",
        "layout_rebuild",
        "refine_35b",
        "rerender",
        "from_blocks",
        "from_cloud",
    }
    if mode not in allowed:
        mode = "layout_content"
    mode = normalize_reprocess_mode(mode)
    try:
        out = reprocess_page(
            session, job, page_no, mode=mode, user_note=user_note  # type: ignore[arg-type]
        )
        session.commit()
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("reprocess failed job=%s page=%s", job_id, page_no)
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc


@router.post("/import/jobs/{job_id}/pages/{page_no}/blocks/{block_id}/refine")
def refine_import_block(
    job_id: str,
    page_no: int,
    block_id: str,
    body: dict | None = None,
    session: Session = Depends(db_session),
) -> dict:
    """Re-recognize one block with 35B (previous block + page image; no MinerU)."""
    from app.services.corpus_reprocess_service import refine_block

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    user_note = ""
    if isinstance(body, dict):
        user_note = str(body.get("user_note") or body.get("note") or "")
    try:
        out = refine_block(session, job, page_no, block_id, user_note=user_note)
        session.commit()
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "block refine failed job=%s page=%s block=%s", job_id, page_no, block_id
        )
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc


@router.get("/import/jobs/{job_id}/pages/{page_no}/rerecognize-context")
def import_rerecognize_context(
    job_id: str,
    page_no: int,
    block_id: str = "",
    session: Session = Depends(db_session),
) -> dict:
    """Structured IR + stage outputs for the Studio re-recognize dialog."""
    from app.services.corpus_reprocess_service import get_rerecognize_context

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return get_rerecognize_context(
            session, job, page_no, block_id=(block_id or "").strip()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/import/jobs/{job_id}/pages/{page_no}/debug")
def import_page_debug_index(
    job_id: str, page_no: int, session: Session = Depends(db_session)
) -> dict:
    from app.services.corpus_debug_bundle import load_page_debug_index

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return load_page_debug_index(job, page_no)


@router.get("/import/jobs/{job_id}/pages/{page_no}/debug/{artifact}")
def import_page_debug_artifact(
    job_id: str,
    page_no: int,
    artifact: str,
    session: Session = Depends(db_session),
) -> dict:
    from app.services.corpus_debug_bundle import load_page_debug_artifact

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return load_page_debug_artifact(job, page_no, artifact)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/import/jobs/{job_id}/pages/{page_no}/feedback")
async def create_import_feedback(
    job_id: str,
    page_no: int,
    request: Request,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import corpus_feedback_store as fb_store
    from app.services.corpus_feedback_service import create_page_feedback

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    content_type = request.headers.get("content-type") or ""
    body = ""
    tags: list[str] = []
    block_id = ""
    screenshot_bytes: bytes | None = None
    screenshot_ct = "image/jpeg"

    if "multipart/form-data" in content_type:
        form = await request.form()
        body = str(form.get("body") or "")
        block_id = str(form.get("block_id") or "")
        raw_tags = form.get("tags")
        if isinstance(raw_tags, str) and raw_tags.strip():
            try:
                parsed = loads(raw_tags, [])
                if isinstance(parsed, list):
                    tags = [str(t) for t in parsed]
            except Exception:
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        file = form.get("screenshot")
        if file is not None and hasattr(file, "read"):
            screenshot_bytes = await file.read()  # type: ignore[misc]
            screenshot_ct = getattr(file, "content_type", None) or "image/jpeg"
    else:
        data = await request.json()
        body = str(data.get("body") or "")
        block_id = str(data.get("block_id") or "")
        tags = [str(t) for t in (data.get("tags") or [])]

    if not body.strip():
        raise HTTPException(status_code=400, detail="body required")

    try:
        row = create_page_feedback(
            session,
            job=job,
            page_no=page_no,
            body=body,
            tags=tags,
            block_id=block_id,
            screenshot_bytes=screenshot_bytes,
            screenshot_content_type=screenshot_ct,
        )
        # Human-gated Edition policy candidate (ops console reviews before publish).
        try:
            from app.services.editions import ops as edition_ops

            meta = loads(job.meta_json, {})
            eid = (
                getattr(job, "edition_id", "")
                or meta.get("edition_id")
                or meta.get("textbook_profile")
                or ""
            )
            if eid:
                edition_ops.enqueue_correction(
                    session,
                    edition_id=str(eid),
                    candidate={
                        "id": f"fb.{row.id[:8]}",
                        "facet": "content_correct",
                        "severity": "may",
                        "statement": (body.strip()[:500]),
                        "tags": ["feedback", f"page:{page_no}", *tags[:5]],
                    },
                    source="feedback",
                    source_ref=row.id,
                )
        except Exception:
            logger.exception("edition correction enqueue failed")
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "feedback": fb_store.feedback_to_dict(row)}


@router.get("/import/jobs/{job_id}/feedback")
def list_import_feedback(
    job_id: str,
    status: str | None = Query(default=None),
    session: Session = Depends(db_session),
) -> dict:
    from app.services import corpus_feedback_store as fb_store

    items = fb_store.list_feedback(session, job_id=job_id, status=status)
    return {
        "job_id": job_id,
        "items": [fb_store.feedback_to_dict(i) for i in items],
    }


@router.get("/import/feedback/{feedback_id}")
def get_import_feedback(
    feedback_id: str, session: Session = Depends(db_session)
) -> dict:
    from app.services import corpus_feedback_store as fb_store

    row = fb_store.get_feedback(session, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    return fb_store.feedback_to_dict(row)


class _CorpusFeedbackPatch(BaseModel):
    status: str | None = None
    resolution_note: str | None = None


@router.patch("/import/feedback/{feedback_id}")
def patch_import_feedback(
    feedback_id: str,
    body: _CorpusFeedbackPatch,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import corpus_feedback_store as fb_store
    from app.services.corpus_feedback_service import patch_feedback

    row = fb_store.get_feedback(session, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    patch_feedback(
        session,
        row,
        status=body.status,
        resolution_note=body.resolution_note,
    )
    session.commit()
    return {"ok": True, "feedback": fb_store.feedback_to_dict(row)}


@router.post("/import/feedback/{feedback_id}/cursor-pack")
def build_import_cursor_pack(
    feedback_id: str, session: Session = Depends(db_session)
) -> dict:
    from app.services import corpus_feedback_store as fb_store
    from app.services.corpus_cursor_pack import build_cursor_pack

    row = fb_store.get_feedback(session, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    try:
        out = build_cursor_pack(session, row)
        session.commit()
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/import/jobs/{job_id}", response_model=ImportJobOut)
def get_import_job(job_id: str, session: Session = Depends(db_session)) -> ImportJobOut:
    out = svc.get_import_job(session, job_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return out


@router.get("/import/jobs/{job_id}/events")
async def import_job_events(job_id: str, session: Session = Depends(db_session)):
    """SSE stream of import progress (Redis pub/sub). Poll REST remains fallback."""
    import asyncio
    import json

    from fastapi.responses import StreamingResponse

    from app.clients.redis_client import IMPORT_EVENTS_CHANNEL, get_redis

    if svc.get_import_job(session, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def gen():
        r = get_redis()
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(IMPORT_EVENTS_CHANNEL)
        try:
            yield f"event: hello\ndata: {json.dumps({'job_id': job_id})}\n\n"
            while True:
                msg = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if msg and msg.get("type") == "message":
                    raw = msg.get("data")
                    try:
                        data = json.loads(raw) if isinstance(raw, str) else raw
                    except Exception:
                        continue
                    if str(data.get("job_id") or "") != job_id:
                        continue
                    et = str(data.get("type") or "job.progress")
                    yield f"event: {et}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    if et == "job.terminal" or str(data.get("status") or "") in {
                        "done",
                        "succeeded",
                        "failed",
                        "error",
                        "canceled",
                        "cancelled",
                        "chunked",
                        "review",
                        "published",
                    }:
                        if et != "job.progress" or str(data.get("status") or "") in {
                            "done",
                            "succeeded",
                            "failed",
                            "error",
                            "canceled",
                            "cancelled",
                            "chunked",
                            "review",
                            "published",
                        }:
                            # Keep stream open briefly for late page.ready; client closes.
                            pass
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.05)
        finally:
            try:
                pubsub.unsubscribe(IMPORT_EVENTS_CHANNEL)
                pubsub.close()
            except Exception:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/import/jobs/{job_id}/markdown", response_model=ImportJobOut)
def patch_import_markdown(
    job_id: str,
    body: MarkdownPatch,
    session: Session = Depends(db_session),
) -> ImportJobOut:
    try:
        return svc.patch_job_markdown(session, job_id, body)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/import/jobs/{job_id}/chunk", response_model=ImportJobOut)
def chunk_import_job(job_id: str, session: Session = Depends(db_session)) -> ImportJobOut:
    try:
        job = run_import_chunk(session, job_id)
        return svc.get_import_job(session, job.id)  # type: ignore[return-value]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class StructureMergeRequest(BaseModel):
    """Re-run TOC / toc_leaf / deepen on an existing import (main pipeline)."""

    purge: bool = True


@router.post("/import/jobs/{job_id}/structure-merge", response_model=ImportJobOut)
def structure_merge_import_job(
    job_id: str,
    body: StructureMergeRequest | None = None,
    session: Session = Depends(db_session),
) -> ImportJobOut:
    """Main-line structure re-run (same deepen as first import).

    ``purge=true`` (default) drops stale TOC/semantic nodes but keeps page_import.
    """
    from app.services.corpus_import_incremental import rerun_import_structure_merge

    purge = True if body is None else bool(body.purge)
    try:
        job = rerun_import_structure_merge(session, job_id, purge=purge)
        session.commit()
        return svc.get_import_job(session, job.id)  # type: ignore[return-value]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("structure-merge failed job=%s", job_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/import/jobs/{job_id}/process", response_model=ImportJobOut)
def process_import(job_id: str, session: Session = Depends(db_session)) -> ImportJobOut:
    try:
        job = process_import_job(session, job_id)
        return svc._job_out(job, session)  # noqa: SLF001
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/import/jobs/{job_id}/cancel")
def cancel_import(job_id: str, session: Session = Depends(db_session)) -> dict:
    """Cooperatively cancel an in-flight import. Worker stops at next page."""
    from app.clients.redis_client import purge_import_queue
    from app.services import pipeline_telemetry

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    pipeline_telemetry.request_cancel(job_id)
    queue_removed = purge_import_queue(job_id)
    if job.status in svc.ACTIVE_IMPORT_STATUSES:
        job.status = "canceled"
        job.error = "已取消（用户操作）"
        job.updated_at = svc._now_iso()  # noqa: SLF001
        session.commit()
    return {
        "job_id": job_id,
        "status": job.status,
        "canceled": True,
        "queue_removed": queue_removed,
    }


@router.delete("/import/jobs/{job_id}")
def delete_import_job(job_id: str, session: Session = Depends(db_session)) -> dict:
    """Hard-delete an import job row + purge its Redis telemetry/cancel keys."""
    try:
        return svc.delete_import_job(session, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/import/jobs/{job_id}/finalize-book")
def finalize_book_preview(job_id: str, session: Session = Depends(db_session)) -> dict:
    """Preview 定稿: which version stays, which get deleted, and the warnings."""
    from app.services.corpus_finalize import preview_finalize

    try:
        return preview_finalize(session, job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/import/jobs/{job_id}/finalize-book")
def finalize_book(
    job_id: str,
    body: FinalizeBookRequest,
    session: Session = Depends(db_session),
) -> dict:
    """定稿 this import: it becomes the book's only version; others are deleted."""
    from app.services.corpus_finalize import finalize_book as run_finalize

    try:
        return run_finalize(
            session,
            job_id,
            note=body.note,
            ack_warnings=body.ack_warnings,
            dry_run=body.dry_run,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("finalize-book failed job=%s", job_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/import/jobs/{job_id}/unfinalize-book")
def unfinalize_book(job_id: str, session: Session = Depends(db_session)) -> dict:
    """Release the 定稿 delete lock (already-deleted versions stay deleted)."""
    from app.services.corpus_finalize import unfinalize_book as run_unfinalize

    try:
        return run_unfinalize(session, job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/pipeline/status")
def pipeline_status(session: Session = Depends(db_session)) -> dict:
    """Real-time pipeline telemetry: live workers + active jobs + per-node detail.

    The import pipeline is a single orchestrated task inside buddy-worker — this
    aggregates worker heartbeats (how many alive, what each is doing) and the
    live phase/sub-progress/error of each active job.
    """
    from app.services import pipeline_telemetry
    from app.services.import_recovery import STALE_SECONDS, job_is_stale

    workers = pipeline_telemetry.list_workers()

    active = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.status.in_(svc.ACTIVE_IMPORT_STATUSES))
        .order_by(CorpusImportJob.created_at.desc())
        .all()
    )
    jobs_out = []
    for job in active:
        meta = loads(job.meta_json, {})
        live = pipeline_telemetry.get_job_live(job.id) or {}
        stale, stale_age_s = job_is_stale(job, workers, stale_seconds=STALE_SECONDS)
        refine_page = int(meta.get("refine_page") or live.get("page") or 0)
        refine_total = int(meta.get("refine_total") or live.get("total") or 0)
        parse_engine = (
            (meta.get("parse_config") or {}).get("engine")
            or meta.get("parse_engine")
            or "mineru_cloud"
        )
        jobs_out.append(
            {
                "id": job.id,
                "label": job.label,
                "status": job.status,
                "parse_engine": parse_engine,
                "textbook_node_id": job.textbook_node_id,
                "pipeline_phase": meta.get("pipeline_phase") or live.get("phase") or "",
                "refine_page": refine_page,
                "refine_total": refine_total,
                "chunk_progress": meta.get("chunk_progress") or "",
                "pages_ready": len(meta.get("pages_ready") or []),
                "block_count": meta.get("block_count") or 0,
                "error": job.error or live.get("error") or "",
                "live": live,
                "updated_at": job.updated_at,
                "stale": stale,
                "stale_age_s": stale_age_s,
            }
        )

    return {
        "workers": workers,
        "worker_count": len(workers),
        "busy_workers": sum(1 for w in workers if w.get("status") == "busy"),
        "active_jobs": jobs_out,
        "active_job_count": len(jobs_out),
        "stale_threshold_s": STALE_SECONDS,
    }


@router.post("/scout", response_model=ScoutRunOut)
def start_scout(body: ScoutRequest, session: Session = Depends(db_session)) -> ScoutRunOut:
    run_out = svc.start_scout(session, body)
    session.commit()
    enqueue_scout_run(run_out.id)
    return run_out


@router.get("/scout-runs", response_model=list[ScoutRunOut])
def list_scout_runs(session: Session = Depends(db_session), limit: int = 50) -> list[ScoutRunOut]:
    from app.db.models import CorpusScoutRun

    rows = session.query(CorpusScoutRun).order_by(CorpusScoutRun.started_at.desc()).limit(limit).all()
    return [svc._scout_out(r) for r in rows]  # noqa: SLF001


@router.get("/inbox", response_model=list[InboxItemOut])
def list_inbox(
    status: str | None = None,
    session: Session = Depends(db_session),
) -> list[InboxItemOut]:
    return svc.list_inbox(session, status=status)


@router.post("/inbox/{item_id}/approve")
def approve_inbox(item_id: str, publish: bool = False, session: Session = Depends(db_session)) -> dict:
    try:
        ids = approve_inbox_item(session, item_id, publish=publish)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"approved": item_id, "node_ids": ids}


@router.post("/inbox/{item_id}/reject")
def reject_inbox(item_id: str, session: Session = Depends(db_session)) -> dict:
    from app.db.models import CorpusInboxItem
    from app.services.corpus_admin import _now_iso

    item = session.get(CorpusInboxItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    item.status = "rejected"
    item.reviewed_at = _now_iso()
    return {"rejected": item_id}


@router.get("/graph", response_model=GraphOut)
def get_graph(
    center: str = "",
    hops: int = 2,
    limit: int = 50,
    include_draft: bool = False,
    rel_types: str | None = None,
    root: str | None = None,
    session: Session = Depends(db_session),
) -> GraphOut:
    return svc.get_graph(
        session,
        center=center,
        hops=hops,
        limit=limit,
        include_draft=include_draft,
        rel_types=rel_types,
        root=root,
    )


@router.post("/promote/from-question/{question_id}", response_model=CorpusNodeOut)
def promote_question(
    question_id: str,
    body: PromoteQuestionRequest,
    session: Session = Depends(db_session),
) -> CorpusNodeOut:
    try:
        return svc.promote_from_question(session, question_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---- Improvement Points (IP) ----


@router.post("/improvement-points/ingest-attempts")
def ip_ingest_attempts(
    body: IpAttemptIngestRequest, session: Session = Depends(db_session)
) -> dict:
    from app.services import improvement_points as ip_svc

    out = ip_svc.ingest_attempts(
        session,
        attempts=body.attempts,
        job_id=body.job_id,
        session_id=body.session_id,
        fingerprint_hash=body.fingerprint_hash,
    )
    session.commit()
    return out


@router.post("/improvement-points/confirm")
def ip_confirm(body: IpConfirmRequest, session: Session = Depends(db_session)) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.confirm_improvement(
            session,
            job_id=body.job_id,
            session_id=body.session_id,
            fingerprint_hash=body.fingerprint_hash,
            page_no=body.page_no,
            category=body.category,
            summary=body.summary,
            authority=body.authority,
            kind=body.kind,
            block_ids=body.block_ids,
            slot=body.slot,
            procedure=body.procedure,
            attempts=body.attempts,
            concrete=body.concrete,
            pattern_abstract=body.pattern_abstract,
            pattern_status=body.pattern_status,
            edition_family_id=body.edition_family_id,
            write_correction=body.write_correction,
            origin=body.origin,
            needs_split=body.needs_split,
        )
    except ip_svc.PrepGateError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.get("/improvement-points/conflict-pairs")
def ip_conflict_pairs(
    fingerprint_hash: str = "",
    detect: bool = True,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.list_conflict_pairs(
            session, fingerprint_hash=fingerprint_hash, detect=detect
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/improvement-points/conflict-resolve")
def ip_conflict_resolve_prep(
    body: IpPrepConflictResolveRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.resolve_prep_conflicts(
            session, keep_ids=body.keep_ids, delete_ids=body.delete_ids
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.get("/improvement-points/list-for-triage")
def ip_list_for_triage(
    fingerprint_hash: str = "",
    limit: int = 2000,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    return ip_svc.list_for_triage(
        session, fingerprint_hash=fingerprint_hash, limit=limit
    )


@router.get("/improvement-points/list-for-split")
def ip_list_for_split(
    fingerprint_hash: str = "",
    limit: int = 2000,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    return ip_svc.list_for_split(
        session, fingerprint_hash=fingerprint_hash, limit=limit
    )


@router.get("/improvement-points/conflict-hints")
def ip_conflict_hints(
    fingerprint_hash: str = "",
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        return ip_svc.conflict_hints(session, fingerprint_hash=fingerprint_hash)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/improvement-points/triage-apply")
def ip_triage_apply(
    body: IpTriageApplyRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.triage_apply(
            session,
            fingerprint_hash=body.fingerprint_hash,
            ready_ids=body.ready_ids,
            split_ids=body.split_ids,
            aggregate_groups=body.aggregate_groups,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/improvement-points/{ip_id}/split-propose")
def ip_split_propose(
    ip_id: str,
    body: IpSplitProposeRequest | None = None,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        children = (body.children if body else None) or None
        return ip_svc.split_propose(
            session, ip_id, children_spec=children or None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/improvement-points/{ip_id}/split-apply")
def ip_split_apply(
    ip_id: str,
    body: IpSplitApplyRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.split_apply(session, ip_id, children=body.children or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/improvement-points/converge")
def ip_converge(body: IpConvergeRequest, session: Session = Depends(db_session)) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.converge_job(
            session,
            job_id=body.job_id,
            session_id=body.session_id,
            fingerprint_hash=body.fingerprint_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.delete("/improvement-points/{ip_id}")
def ip_purge(ip_id: str, session: Session = Depends(db_session)) -> dict:
    """彻底删除改进点及其派生残留（专书页补丁、链接、待审队列引用等）。"""
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.purge_improvement_point(session, ip_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    session.commit()
    return out


@router.post("/improvement-points/purge-without-procedure")
def ip_purge_without_procedure(session: Session = Depends(db_session)) -> dict:
    """批量硬删缺少成功实现路径的改进点。"""
    from app.services import improvement_points as ip_svc

    out = ip_svc.purge_ips_without_success_procedure(session)
    session.commit()
    return out


@router.post("/improvement-points/{ip_id}/resolve-conflict")
def ip_resolve_conflict(
    ip_id: str,
    body: IpResolveConflictRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.resolve_conflict(session, ip_id, winner=body.winner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/improvement-points/{ip_id}/pattern")
def ip_set_pattern(
    ip_id: str,
    body: IpPatternRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.set_pattern(
            session,
            ip_id,
            pattern_status=body.pattern_status,
            abstract_text=body.abstract_text,
            target_layer=body.target_layer,
            edition_family_id=body.edition_family_id,
            suspect_type=body.suspect_type,
        )
    except ip_svc.PrepGateError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.get("/patterns")
def pattern_list(
    category: str = "",
    status: str = "",
    landing_target: str = "",
    min_members: int = 0,
    with_members: bool = False,
    limit: int = 200,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    return ip_svc.list_patterns(
        session,
        category=category,
        status=status,
        landing_target=landing_target,
        min_members=min_members,
        with_members=with_members,
        limit=limit,
    )


@router.get("/patterns/{pattern_id}")
def pattern_get(pattern_id: str, session: Session = Depends(db_session)) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        return ip_svc.get_pattern(session, pattern_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/patterns/{pattern_id}/route")
def pattern_route(
    pattern_id: str,
    body: PatternRouteRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.route_pattern(
            session, pattern_id, landing_target=body.landing_target, note=body.note
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/patterns/{pattern_id}/land")
def pattern_land(
    pattern_id: str,
    body: PatternLandRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.land_pattern(
            session, pattern_id, landed_ref=body.landed_ref, note=body.note
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/patterns/{pattern_id}/merge")
def pattern_merge(
    pattern_id: str,
    body: PatternMergeRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.merge_patterns(
            session, keep_id=pattern_id, merge_ids=body.merge_ids, note=body.note
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/patterns/{pattern_id}/split")
def pattern_split(
    pattern_id: str,
    body: PatternSplitRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.split_pattern(
            session,
            pattern_id=pattern_id,
            member_ip_ids=body.member_ip_ids,
            abstract_text=body.abstract_text,
            category=body.category,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.post("/patterns/{pattern_id}/bind-detector")
def pattern_bind_detector(
    pattern_id: str,
    body: PatternBindDetectorRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import corpus_symptom_ops as sym_ops

    try:
        out = sym_ops.bind_detector(
            session,
            pattern_id=pattern_id,
            suspect_type=body.suspect_type,
            detector_ref=body.detector_ref,
            remediation_kind=body.remediation_kind,
            playbook_ref=body.playbook_ref,
            landing_target=body.landing_target,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.get("/patterns/{pattern_id}/promotion-check")
def pattern_promotion_check(
    pattern_id: str, session: Session = Depends(db_session)
) -> dict:
    """够不够格从 playbook 升 rule。只出提议，不写规则文件。"""
    from app.services import corpus_symptom_ops as sym_ops

    try:
        out = sym_ops.promotion_check(session, pattern_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    session.commit()
    return out


@router.get("/checks")
def checks_list(session: Session = Depends(db_session)) -> dict:
    """当前生效的症状检测器 + 可用词表（含体检）+ 没能生效的声明（load_errors）。"""
    from app.services import corpus_symptom_ops as sym_ops

    return sym_ops.checks_overview(session)


@router.post("/checks/dry-run")
def checks_dry_run(
    body: CheckDryRunRequest, session: Session = Depends(db_session)
) -> dict:
    """在一次真实导入的页上试跑一条检测器。不落库、不改文件。"""
    from app.services import corpus_symptom_ops as sym_ops

    try:
        return sym_ops.dry_run(
            session,
            job_id=body.job_id,
            suspect_type=body.suspect_type,
            yaml_text=body.yaml_text,
            pages=body.pages,
            limit=body.limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/checks/run-examples")
def checks_run_examples(body: CheckExamplesRequest) -> dict:
    """跑正反例，给出召回率与误报率。"""
    from app.services import corpus_symptom_ops as sym_ops

    try:
        return sym_ops.run_examples(
            suspect_type=body.suspect_type, yaml_text=body.yaml_text
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/checks/false-positive")
def checks_report_false_positive(
    body: CheckFalsePositiveRequest, session: Session = Depends(db_session)
) -> dict:
    """人裁决「这不是问题」。只降权不屏蔽。"""
    from app.services import corpus_symptom_ops as sym_ops

    try:
        out = sym_ops.report_false_positive(
            session,
            suspect_type=body.suspect_type,
            fingerprint=body.fingerprint,
            job_id=body.job_id,
            page_no=body.page_no,
            block_id=body.block_id,
            evidence=body.evidence,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


def _job_symptoms_payload(job: CorpusImportJob) -> dict:
    """读任务元数据，按「有没有处置办法」切开。"""
    from app.services.corpus_symptoms import partition_findings

    meta = loads(job.meta_json, {}) or {}
    findings = list(meta.get("symptom_findings") or [])
    known, unknown = partition_findings(findings)
    return {
        "job_id": job.id,
        "summary": meta.get("symptom_summary") or {},
        "coverage": meta.get("layout_escalate_target") or "none",
        "known": known,
        "unknown": unknown,
        "refreshed_at": meta.get("symptom_refreshed_at") or "",
        # 数值兜底，和命名症状并列而不混为一谈。
        "low_confidence_pages": sorted(
            int(p) for p in (meta.get("layout_escalate_pages") or [])
        ),
    }


@router.get("/jobs/{job_id}/symptoms")
def job_symptoms(job_id: str, session: Session = Depends(db_session)) -> dict:
    """这次导入查出的命名症状，按「有成功路径 / 需诊断」分开。"""
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_symptoms_payload(job)


@router.post("/jobs/{job_id}/symptoms/refresh")
def job_symptoms_refresh(job_id: str, session: Session = Depends(db_session)) -> dict:
    """按当前中间表示整本重跑检测，覆盖导入时的症状快照。

    自动改每轮结束必须走这里：编排器用活清单决定能不能收口，
    Studio 中栏也不再钉死导入那一刻的卡片数。
    """
    from app.services.import_ir_pipeline import refresh_job_symptom_findings

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        refresh_job_symptom_findings(job)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return _job_symptoms_payload(job)


@router.get("/improvement-points/books")
def ip_books(session: Session = Depends(db_session)) -> dict:
    """改进点侧栏：按真实书指纹分组（含无对应导入任务的孤儿指纹）。"""
    from app.services import improvement_points as ip_svc

    return ip_svc.list_ip_books(session)


@router.get("/improvement-points")
def ip_list(
    fingerprint_hash: str = "",
    job_id: str = "",
    status: str = "",
    prep_status: str = "",
    page_no: int | None = None,
    limit: int = 1000,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    return ip_svc.list_improvement_points(
        session,
        fingerprint_hash=fingerprint_hash,
        job_id=job_id,
        status=status,
        prep_status=prep_status,
        page_no=page_no,
        limit=limit,
    )


@router.post("/books/{fingerprint_hash}/bind-edition")
def book_bind_edition(
    fingerprint_hash: str,
    body: BookEditionBindRequest,
    session: Session = Depends(db_session),
) -> dict:
    from app.services import improvement_points as ip_svc

    try:
        out = ip_svc.bind_book_edition(
            session,
            fingerprint_hash,
            body.edition_id,
            source_job_id=body.source_job_id,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    return out


@router.get("/books/{fingerprint_hash}/bind-edition")
def book_get_bind(
    fingerprint_hash: str, session: Session = Depends(db_session)
) -> dict:
    from app.services import improvement_points as ip_svc

    out = ip_svc.get_book_edition_bind(session, fingerprint_hash)
    if out is None:
        raise HTTPException(status_code=404, detail="no bind")
    return out
