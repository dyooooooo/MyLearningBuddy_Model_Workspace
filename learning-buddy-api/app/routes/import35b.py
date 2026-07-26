"""Isolated HTTP API + self-contained preview for the 35B import line.

Zero coupling to corpus_admin / Studio. Everything the 35B line needs is served
here: create/list/get/delete jobs, per-page HTML, figure assets, CSS, and a
standalone preview page (inline MathJax bootstrap) that renders directly from
import35b_pages — no Studio frontend required.
"""
from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.clients.redis_client import enqueue_import35b_job
from app.db.session import db_session
from app.import_35b import service, store
from app.import_35b.assets import assert_allowed_asset_key
from app.import_35b.cursor_pack.builder import build_cursor_pack
from app.import_35b.feedback_service import create_page_feedback, patch_feedback
from app.import_35b.reprocess_service import refine_block, reprocess_page
from app.util.json_util import loads

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/import35b", tags=["import35b"])


class Import35bCreate(BaseModel):
    minio_key: str
    subject: str = "math"
    grade_band: str = "hs"
    label: str = ""
    profile_id: str = Field(..., min_length=1, description="Edition bundle id (hard-break)")
    page_limit: int | None = Field(default=None, ge=1)


class Import35bJobOut(BaseModel):
    id: str
    textbook_key: str
    status: str
    subject: str
    grade_band: str
    label: str = ""
    minio_key: str = ""
    page_count: int = 0
    page_limit: int | None = None
    profile_id: str = ""
    error: str = ""
    meta: dict = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


def _job_out(job) -> Import35bJobOut:
    return Import35bJobOut(
        id=job.id,
        textbook_key=job.textbook_key,
        status=job.status,
        subject=job.subject,
        grade_band=job.grade_band,
        label=job.label,
        minio_key=job.minio_key,
        page_count=job.page_count,
        page_limit=job.page_limit,
        profile_id=job.profile_id,
        error=job.error,
        meta=loads(job.meta_json, {}),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/jobs", response_model=Import35bJobOut)
def create_job(body: Import35bCreate, session: Session = Depends(db_session)) -> Import35bJobOut:
    job = service.create_import(
        session,
        minio_key=body.minio_key,
        subject=body.subject,
        grade_band=body.grade_band,
        label=body.label,
        profile_id=body.profile_id,
        page_limit=body.page_limit,
    )
    out = _job_out(job)
    session.commit()
    enqueue_import35b_job(out.id)
    return out


@router.get("/jobs", response_model=list[Import35bJobOut])
def list_jobs(session: Session = Depends(db_session)) -> list[Import35bJobOut]:
    return [_job_out(j) for j in store.list_jobs(session)]


@router.get("/jobs/{job_id}", response_model=Import35bJobOut)
def get_job(job_id: str, session: Session = Depends(db_session)) -> Import35bJobOut:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, session: Session = Depends(db_session)) -> dict:
    result = service.purge_import_job(session, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    return result


@router.get("/jobs/{job_id}/pages")
def list_pages(job_id: str, session: Session = Depends(db_session)) -> dict:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    pages = store.get_pages(session, job.textbook_key)
    return {
        "job_id": job_id,
        "textbook_key": job.textbook_key,
        "status": job.status,
        "page_count": len(pages),
        "pages": [{"page_no": p.page_no, "chars": len(p.plain_text or "")} for p in pages],
    }


@router.get("/jobs/{job_id}/page/{page_no}/html")
def get_page_html(
    job_id: str, page_no: int, session: Session = Depends(db_session)
) -> HTMLResponse:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    page = store.get_page(session, job.textbook_key, page_no)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return HTMLResponse(content=page.html)


@router.get("/jobs/{job_id}/page/{page_no}/blueprint")
def get_page_blueprint(
    job_id: str,
    page_no: int,
    block_id: str = Query(default=""),
    session: Session = Depends(db_session),
) -> dict:
    """结构化输出原文（落盘 blueprint）——供审核对话框排查「结构化 vs 渲染」。

    ``block_id`` 给定时只回该块；命中不到则回整页 blocks 供人工比对。
    """
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from app.import_35b.review_service import _load_page_blueprint_dict

    bp = _load_page_blueprint_dict(job, page_no)
    if bp is None:
        raise HTTPException(status_code=404, detail="blueprint not found")
    if block_id:
        for b in bp.get("blocks") or []:
            if isinstance(b, dict) and str(b.get("id") or "") == block_id:
                return {"page_no": page_no, "block_id": block_id, "block": b}
        return {"page_no": page_no, "block_id": block_id, "block": None, "blocks": bp.get("blocks")}
    return {"page_no": page_no, "block_id": "", "page": bp}


@router.get("/jobs/{job_id}/page/{page_no}/debug")
def get_page_debug_index(
    job_id: str,
    page_no: int,
    session: Session = Depends(db_session),
) -> dict:
    """Per-page pipeline debug index (artifacts availability + changelog summary)."""
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from app.import_35b.debug_bundle import load_page_debug_index

    return load_page_debug_index(job, page_no)


@router.get("/jobs/{job_id}/page/{page_no}/debug/changelog")
def get_page_debug_changelog(
    job_id: str,
    page_no: int,
    session: Session = Depends(db_session),
) -> dict:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from app.import_35b.debug_bundle import load_page_debug_changelog

    changelog = load_page_debug_changelog(job, page_no)
    if changelog is None:
        raise HTTPException(status_code=404, detail="changelog not found")
    return changelog


@router.get("/jobs/{job_id}/page/{page_no}/debug/page-image")
def get_page_debug_image(
    job_id: str,
    page_no: int,
    session: Session = Depends(db_session),
) -> Response:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from app.import_35b.debug_bundle import load_page_debug_image

    data = load_page_debug_image(job, page_no)
    if not data:
        raise HTTPException(status_code=404, detail="page image not found")
    return Response(content=data, media_type="image/jpeg")


@router.get("/jobs/{job_id}/page/{page_no}/debug/{artifact}")
def get_page_debug_artifact(
    job_id: str,
    page_no: int,
    artifact: str,
    session: Session = Depends(db_session),
) -> Response:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from app.import_35b.debug_bundle import load_page_debug_artifact

    try:
        content_type, body = load_page_debug_artifact(job, page_no, artifact)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if content_type == "application/json":
        return Response(
            content=json.dumps(body, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    if isinstance(body, bytes):
        return Response(content=body, media_type=content_type)
    return Response(content=str(body).encode("utf-8"), media_type=content_type)


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


@router.get("/asset")
def get_asset(key: str = Query(..., min_length=1)) -> Response:
    try:
        assert_allowed_asset_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    data = get_object_bytes(key)
    if not data:
        raise HTTPException(status_code=404, detail="asset not found")
    return Response(content=data, media_type=_asset_content_type(key))


# --- Page reprocess + block refine -----------------------------------------


class ReprocessBody(BaseModel):
    mode: Literal["full", "from_raw"] = "full"
    reason: str = "user_request"
    feedback_id: str | None = None


@router.post("/jobs/{job_id}/pages/{page_no}/reprocess")
def reprocess_page_route(
    job_id: str,
    page_no: int,
    body: ReprocessBody | None = None,
    session: Session = Depends(db_session),
) -> dict:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    payload = body or ReprocessBody()
    try:
        return reprocess_page(
            job_id,
            page_no,
            mode=payload.mode,
            reason=payload.reason,
            feedback_id=payload.feedback_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class RefineBlockBody(BaseModel):
    note: str = ""
    feedback_id: str | None = None


@router.post("/jobs/{job_id}/pages/{page_no}/blocks/{block_id}/refine")
def refine_block_route(
    job_id: str,
    page_no: int,
    block_id: str,
    body: RefineBlockBody | None = None,
    session: Session = Depends(db_session),
) -> dict:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    payload = body or RefineBlockBody()
    try:
        return refine_block(
            job_id,
            page_no,
            block_id,
            note=payload.note,
            feedback_id=payload.feedback_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --- User feedback + Cursor Repair Pack ------------------------------------


class FeedbackPatch(BaseModel):
    status: str | None = None
    resolution_note: str | None = None


@router.post("/jobs/{job_id}/pages/{page_no}/feedback")
async def create_feedback(
    job_id: str,
    page_no: int,
    body: str = Form(...),
    tags: str = Form(default="[]"),
    block_id: str = Form(default=""),
    screenshot: UploadFile | None = File(default=None),
    session: Session = Depends(db_session),
) -> dict:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not body.strip():
        raise HTTPException(status_code=422, detail="body required")
    try:
        tag_list = json.loads(tags) if tags else []
        if not isinstance(tag_list, list):
            tag_list = []
    except json.JSONDecodeError:
        tag_list = []
    shot_bytes: bytes | None = None
    shot_ct = "image/jpeg"
    if screenshot is not None and screenshot.filename:
        shot_bytes = await screenshot.read()
        if screenshot.content_type:
            shot_ct = screenshot.content_type
    try:
        row = create_page_feedback(
            session,
            job=job,
            page_no=page_no,
            body=body,
            tags=[str(t) for t in tag_list],
            block_id=block_id,
            screenshot_bytes=shot_bytes,
            screenshot_content_type=shot_ct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return {"ok": True, "feedback": store.feedback_to_dict(row)}


@router.get("/jobs/{job_id}/feedback")
def list_job_feedback(
    job_id: str,
    status: str | None = Query(default=None),
    session: Session = Depends(db_session),
) -> dict:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    items = store.list_feedback(session, job_id=job_id, status=status)
    open_count = sum(1 for i in items if i.status == "open")
    return {
        "job_id": job_id,
        "open_count": open_count,
        "items": [store.feedback_to_dict(i) for i in items],
    }


@router.get("/feedback/{feedback_id}")
def get_feedback(feedback_id: str, session: Session = Depends(db_session)) -> dict:
    row = store.get_feedback(session, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    return store.feedback_to_dict(row)


@router.patch("/feedback/{feedback_id}")
def update_feedback(
    feedback_id: str,
    body: FeedbackPatch,
    session: Session = Depends(db_session),
) -> dict:
    row = store.get_feedback(session, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    patch_feedback(
        session,
        row,
        status=body.status,
        resolution_note=body.resolution_note,
    )
    session.commit()
    return {"ok": True, "feedback": store.feedback_to_dict(row)}


@router.post("/feedback/{feedback_id}/cursor-pack")
def generate_cursor_pack(
    feedback_id: str, session: Session = Depends(db_session)
) -> dict:
    row = store.get_feedback(session, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    try:
        result = build_cursor_pack(session, row)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return result


class UserReport(BaseModel):
    block_id: str = ""
    description: str
    suspect_type: str = "USER_REPORTED"


@router.post("/jobs/{job_id}/pages/{page_no}/report")
def report_issue(
    job_id: str, page_no: int, body: UserReport, session: Session = Depends(db_session)
) -> dict:
    """Deprecated — use POST .../feedback. Kept for Studio transition."""
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    row = create_page_feedback(
        session,
        job=job,
        page_no=page_no,
        body=body.description,
        tags=["other"],
        block_id=body.block_id,
    )
    session.commit()
    return {"ok": True, "created": 1, "feedback_id": row.id}


@router.get("/textbook.css")
def get_css() -> Response:
    css_path = Path(__file__).resolve().parents[1] / "static" / "textbook-hujiao.css"
    if not css_path.is_file():
        return Response(content="", media_type="text/css")
    return Response(content=css_path.read_text(encoding="utf-8"), media_type="text/css")


_PREVIEW_HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>35B 预览 · {title}</title>
<link rel="stylesheet" href="/api/v1/admin/import35b/textbook.css">
<style>
  body {{ margin:0; background:#f3f4f6; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .lb-toolbar {{ position:sticky; top:0; z-index:20; background:#111827; color:#e5e7eb;
    padding:10px 18px; font-size:14px; display:flex; gap:16px; align-items:center; }}
  .lb-toolbar b {{ color:#fff; }}
  .lb-pages {{ max-width:1180px; margin:0 auto; padding:24px 12px 80px; }}
  .lb-page {{ background:#fff; margin:0 auto 28px; padding:32px 36px; border-radius:8px;
    box-shadow:0 1px 4px rgba(0,0,0,.12); max-width:1080px; }}
  .lb-page-tag {{ font-size:12px; color:#9ca3af; margin:0 auto 6px; max-width:1080px; }}
  .lb-page--blank {{ opacity:.55; }}
</style>
<script>
  window.MathJax = {{
    tex: {{ inlineMath: [['\\\\(','\\\\)']], displayMath: [['\\\\[','\\\\]']] }},
    startup: {{ typeset: false }}
  }};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
</head>
<body>
"""

_PREVIEW_TAIL = """
<script>
  function hydrateMath() {
    document.querySelectorAll('span.math[data-latex]').forEach(function (el) {
      if (el.dataset.hydrated) return;
      var tex = el.getAttribute('data-latex') || '';
      var display = el.classList.contains('math--display');
      el.textContent = display ? '\\\\[' + tex + '\\\\]' : '\\\\(' + tex + '\\\\)';
      el.dataset.hydrated = '1';
    });
  }
  function typeset() {
    hydrateMath();
    if (window.MathJax && window.MathJax.typesetPromise) {
      window.MathJax.typesetPromise();
    }
  }
  if (document.readyState !== 'loading') { typeset(); }
  else { document.addEventListener('DOMContentLoaded', typeset); }
  window.addEventListener('load', typeset);
</script>
</body>
</html>
"""


@router.get("/jobs/{job_id}/preview", response_class=HTMLResponse)
def preview(job_id: str, session: Session = Depends(db_session)) -> HTMLResponse:
    job = store.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    pages = store.get_pages(session, job.textbook_key)
    title = escape(job.label or job.textbook_key)
    parts = [_PREVIEW_HEAD.format(title=title)]
    meta = loads(job.meta_json, {})
    progress = escape(str(meta.get("progress", "")))
    parts.append(
        f'<div class="lb-toolbar"><b>{title}</b>'
        f'<span>状态: {escape(job.status)}</span>'
        f'<span>页数: {len(pages)}</span>'
        f'<span>进度: {progress or "-"}</span>'
        f'<span>textbook_key: {escape(job.textbook_key)}</span></div>'
    )
    parts.append('<div class="lb-pages">')
    if not pages:
        parts.append('<p style="text-align:center;color:#6b7280;padding:60px;">暂无已完成页面（导入进行中或失败）。</p>')
    for p in pages:
        parts.append(f'<div class="lb-page-tag">第 {p.page_no} 页</div>')
        parts.append(p.html)
    parts.append("</div>")
    parts.append(_PREVIEW_TAIL)
    return HTMLResponse(content="".join(parts))
