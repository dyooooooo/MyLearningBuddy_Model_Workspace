from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app import (
    approvals,
    bus,
    checkpoints,
    config_store,
    conversations,
    hooks_store,
    inbox,
    jobs,
    library,
    models_catalog,
    plugins_store,
    prompts_store,
    sdk_runner,
    surfaces,
    tasks,
)
from app.bridge_cleanup import close_sdk_bridge, sweep_orphan_bridges
from app.config import settings
from app.cursor_auth import ensure_cursor_credentials
from app.sdk_client import close_owned_client, ensure_client
from app.paths import PathJailError
from app.sdk_patches import apply_cursor_sdk_patches
from app.workspace import ensure_workspace, list_textbook_files

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    apply_cursor_sdk_patches()
    try:
        n = sweep_orphan_bridges()
        logger.info("bridge sweep startup: %s orphan process(es)", n)
    except Exception:
        logger.warning("bridge sweep startup failed", exc_info=True)
    ensure_workspace()
    try:
        ensure_cursor_credentials()
    except Exception:
        logger.warning("cursor credential seed failed", exc_info=True)
    try:
        ensure_client()
    except Exception:
        logger.warning("CursorClient.launch_bridge failed; first turn will retry", exc_info=True)
    try:
        checkpoints.backfill_missing_jobs()
    except Exception:
        logger.warning("checkpoint backfill failed", exc_info=True)
    try:
        jobs.cancel_active("interrupted")
    except Exception:
        logger.warning("stale job cancel failed", exc_info=True)
    stop_tick = threading.Event()

    def _tick_loop() -> None:
        while True:
            try:
                tasks.tick()
            except Exception:
                logger.warning("task tick failed", exc_info=True)
            if stop_tick.wait(30):
                break

    threading.Thread(target=_tick_loop, name="task-tick", daemon=True).start()
    from app import page_watch

    if settings.page_sync_watch:
        try:
            page_watch.start()
        except Exception:
            logger.warning("page pair watch failed", exc_info=True)
    yield
    try:
        page_watch.stop()
    except Exception:
        logger.warning("page pair watch stop failed", exc_info=True)
    stop_tick.set()
    try:
        close_owned_client()
    except Exception:
        logger.warning("close_owned_client failed", exc_info=True)
        try:
            close_sdk_bridge()
        except Exception:
            logger.warning("close_sdk_bridge failed", exc_info=True)
    try:
        n = sweep_orphan_bridges()
        logger.info("bridge sweep shutdown: %s orphan process(es)", n)
    except Exception:
        logger.warning("bridge sweep shutdown failed", exc_info=True)


app = FastAPI(title="Textbook File Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _bearer(authorization: str = Header(default="")) -> None:
    expected = (settings.textbook_agent_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="TEXTBOOK_AGENT_TOKEN 未配置")
    got = ""
    if authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    if not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="Invalid token")


class GradeIn(BaseModel):
    request_id: str
    subject: str = "math"
    grade: str = "hs"
    textbook_hint: str = ""
    problem_text: str
    student_answer: str = ""


class ChatImage(BaseModel):
    data: str
    mime_type: str = "image/png"
    name: str = ""


class ChatFile(BaseModel):
    data: str
    name: str
    mime_type: str = "application/octet-stream"


class ModelParamIn(BaseModel):
    id: str
    value: str


class ChatIn(BaseModel):
    message: str = ""
    conversation_id: str = ""
    mode: str = "agent"
    model: str = ""
    plan_id: str = ""
    task_id: str = ""
    trigger: str = ""
    model_params: list[ModelParamIn] = Field(default_factory=list)
    images: list[ChatImage] = Field(default_factory=list)
    files: list[ChatFile] = Field(default_factory=list)
    book_id: str = ""
    book: str = ""
    page_no: int = 0
    block_id: str = ""
    block_label: str = ""
    quote: str = ""
    title: str = ""


def _chat_focus(body: ChatIn) -> dict[str, Any]:
    focus: dict[str, Any] = {}
    book_id = (body.book_id or "").strip()
    book = (body.book or "").strip()
    block_id = (body.block_id or "").strip()
    block_label = (body.block_label or "").strip()
    quote = (body.quote or "").strip()[:400]
    if book_id:
        focus["book_id"] = book_id
    if book:
        focus["book"] = book
    if body.page_no > 0:
        focus["page_no"] = int(body.page_no)
    if block_id:
        focus["block_id"] = block_id
    if block_label:
        focus["block_label"] = block_label
    if quote:
        focus["quote"] = quote
    return focus


class ApprovalDecideIn(BaseModel):
    choice: str
    job_id: str = ""
    note: str = ""


class McpIn(BaseModel):
    mcpServers: dict[str, Any] = Field(default_factory=dict)


class ContentIn(BaseModel):
    content: str


class ToolsIn(BaseModel):
    tools: list[Any] = Field(default_factory=list)


class ToolFileIn(BaseModel):
    path: str = ""
    description: str = ""
    content: str = ""


class LibraryPatchIn(BaseModel):
    label: str | None = None
    textbook_hint: str | None = None
    subject: str | None = None
    grade_band: str | None = None
    category: str | None = None
    is_default: bool | None = None
    status: str | None = None
    conversation_id: str | None = None


class SurfacePrepareIn(BaseModel):
    markdown: str = ""
    printToPdf: dict[str, int] = Field(default_factory=dict)
    bookId: str = ""


class SurfacePaintIn(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class HooksIn(BaseModel):
    version: int = 1
    hooks: dict[str, Any] = Field(default_factory=dict)


class PluginGitIn(BaseModel):
    url: str
    ref: str = ""


class PluginEnabledIn(BaseModel):
    enabled: bool = True


class CheckpointIn(BaseModel):
    kind: str = "manual"
    note: str = ""
    job_id: str = ""
    conversation_id: str = ""


class ForkIn(BaseModel):
    name: str = ""


class TaskIn(BaseModel):
    title: str
    mode: str = "trigger"
    trigger: str = ""
    cron: str = ""
    next_run: str = ""
    message: str = ""
    condition: str = ""
    action: str = "chat"
    prompt: str = ""
    enabled: bool = True


class TaskPatchIn(BaseModel):
    title: str | None = None
    mode: str | None = None
    trigger: str | None = None
    cron: str | None = None
    next_run: str | None = None
    message: str | None = None
    condition: str | None = None
    action: str | None = None
    prompt: str | None = None
    enabled: bool | None = None


class TaskRunIn(BaseModel):
    book_id: str = ""
    message: str = ""


class TaskEmitIn(BaseModel):
    event: str
    book_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "textbook-agent"}


@app.get("/v1/cursor/auth", dependencies=[Depends(_bearer)])
def cursor_auth_status() -> dict[str, Any]:
    """Cloud key probe. Compose healthcheck stays on /health so Cursor outage does not restart the box."""
    from app.cursor_auth import probe_status

    return probe_status(force=True)


@app.post("/v1/grade", status_code=202, dependencies=[Depends(_bearer)])
def grade(body: GradeIn) -> dict[str, str]:
    payload = body.model_dump()
    row = jobs.create_job("grade", payload)
    job_id = row["job_id"]
    checkpoints.snapshot_turn(job_id, "", str(payload.get("problem_text") or "批改")[:80])
    jobs.start_background(job_id, lambda: sdk_runner.run_grade(payload, job_id=job_id))
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/models", dependencies=[Depends(_bearer)])
def list_models() -> dict[str, Any]:
    return {"models": models_catalog.list_cursor_models(), "default": settings.cursor_model}


@app.get("/v1/inbox/{name}", dependencies=[Depends(_bearer)])
def get_inbox(name: str) -> Response:
    try:
        data, mime = inbox.get_file(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="attachment not found") from exc
    except PathJailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type=mime)


@app.post("/v1/chat", status_code=202, dependencies=[Depends(_bearer)])
def chat(body: ChatIn) -> dict[str, str]:
    mode = (body.mode or "agent").strip().lower()
    if mode not in ("agent", "plan", "ask"):
        raise HTTPException(status_code=400, detail="mode must be agent, plan, or ask")
    if not body.message.strip() and not body.images and not body.files:
        raise HTTPException(status_code=400, detail="message or attachment required")
    try:
        inbox.assert_quota(len(body.images) + len(body.files))
        saved_files = inbox.save_items([row.model_dump() for row in body.files], kind="file")
        saved_images = inbox.save_items([row.model_dump() for row in body.images], kind="image")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PathJailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    attachments = saved_images + saved_files
    focus = _chat_focus(body)
    want_title = (body.title or "").strip() or (conversations.focus_title(focus) if focus else "")
    conv = conversations.ensure(body.conversation_id, body.message, title=want_title, focus=focus or None)
    cid = str(conv["id"])
    stored = {
        "message": body.message,
        "conversation_id": cid,
        "mode": mode,
        "model": (body.model or "").strip(),
        "plan_id": (body.plan_id or "").strip(),
        "task_id": (body.task_id or "").strip(),
        "trigger": (body.trigger or "").strip(),
        "model_params": [row.model_dump() for row in body.model_params],
        "attachments": [{k: v for k, v in row.items() if k != "mime_type"} for row in attachments],
        **focus,
    }
    row = jobs.create_job("chat", stored)
    job_id = row["job_id"]
    conversations.attach_job(cid, job_id)
    checkpoints.snapshot_turn(job_id, cid, body.message)
    image_payload = [row.model_dump() for row in body.images]

    def _run() -> dict:
        return sdk_runner.run_chat(
            body.message,
            cid,
            job_id=job_id,
            mode=mode,
            model=body.model,
            model_params=[row.model_dump() for row in body.model_params],
            images=image_payload,
            attachments=attachments,
        )

    jobs.start_background(job_id, _run)
    return {"job_id": job_id, "status": "queued", "conversation_id": cid, "title": conv["title"]}


@app.get("/v1/tasks", dependencies=[Depends(_bearer)])
def list_task_rows() -> dict[str, Any]:
    return {"tasks": tasks.list_tasks()}


@app.get("/v1/tasks/events", dependencies=[Depends(_bearer)])
def list_task_events() -> dict[str, Any]:
    return {"events": tasks.list_events()}


@app.get("/v1/tasks/{task_id}", dependencies=[Depends(_bearer)])
def get_task_row(task_id: str) -> dict[str, Any]:
    row = tasks.get_task_detail(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return row


@app.post("/v1/tasks", dependencies=[Depends(_bearer)])
def create_task_row(body: TaskIn) -> dict[str, Any]:
    try:
        return tasks.create_task(
            title=body.title,
            mode=body.mode,
            trigger=body.trigger,
            cron=body.cron,
            next_run=body.next_run,
            message=body.message,
            condition=body.condition,
            action=body.action,
            prompt=body.prompt,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/v1/tasks/{task_id}", dependencies=[Depends(_bearer)])
def patch_task_row(task_id: str, body: TaskPatchIn) -> dict[str, Any]:
    try:
        row = tasks.update_task(task_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return row


@app.delete("/v1/tasks/{task_id}", dependencies=[Depends(_bearer)])
def cancel_task_row(task_id: str) -> dict[str, Any]:
    try:
        return tasks.cancel_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/tasks/{task_id}/run", dependencies=[Depends(_bearer)])
def run_task_row(task_id: str, body: TaskRunIn | None = None) -> dict[str, Any]:
    row = tasks.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    extra = body.model_dump() if body else {}
    try:
        job = tasks.start_task(row, extra)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=400, detail="task did not start (missing book_id?)")
    return job


@app.post("/v1/tasks/emit", dependencies=[Depends(_bearer)])
def emit_task_event(body: TaskEmitIn) -> dict[str, Any]:
    payload = dict(body.payload or {})
    if body.book_id:
        payload.setdefault("book_id", body.book_id)
    started = tasks.emit(body.event, payload)
    return {"event": body.event, "started": started}


@app.get("/v1/jobs", dependencies=[Depends(_bearer)])
def list_jobs(limit: int = Query(default=80, ge=1, le=200)) -> dict[str, Any]:
    return {"jobs": jobs.list_jobs(limit)}


@app.get("/v1/conversations", dependencies=[Depends(_bearer)])
def list_conversation_threads(limit: int = Query(default=80, ge=1, le=200)) -> dict[str, Any]:
    rows = conversations.list_conversations(limit)
    for row in rows:
        if row.get("title_source") != "agent":
            conversations.schedule_title(str(row["id"]), sdk_runner.summarize_title)
    return {"conversations": rows}


@app.get("/v1/conversations/{conversation_id}", dependencies=[Depends(_bearer)])
def get_conversation_thread(conversation_id: str) -> dict[str, Any]:
    row = conversations.get(conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"conversation": conversations.public_view(row), "jobs": conversations.conversation_jobs(conversation_id)}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(_bearer)])
def get_job(job_id: str) -> dict[str, Any]:
    row = jobs.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    from app import plans

    plans.hydrate(row)
    return jobs.public_view(row)


@app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(_bearer)])
def cancel_job(job_id: str) -> dict[str, Any]:
    row = jobs.request_cancel(job_id, "stopped")
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return row


@app.get("/v1/approvals", dependencies=[Depends(_bearer)])
def list_approvals() -> dict[str, Any]:
    return {"pending": approvals.list_pending()}


@app.post("/v1/approvals/{req_id}/decide", dependencies=[Depends(_bearer)])
def decide_approval(req_id: str, body: ApprovalDecideIn) -> dict[str, Any]:
    choice = (body.choice or "").strip().lower()
    if choice not in ("grant", "refuse", "revise"):
        raise HTTPException(status_code=400, detail="choice must be grant, refuse, or revise")
    note = (body.note or "").strip()
    if choice == "revise" and not note:
        raise HTTPException(status_code=400, detail="revise 需要填写想法")
    job_id = (body.job_id or "").strip()
    conversation_id = ""
    if job_id:
        row = jobs.get_job(job_id) or {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        conversation_id = str((payload or {}).get("conversation_id") or "")
    try:
        if choice == "grant":
            record = approvals.grant(req_id, job_id, conversation_id)
        elif choice == "refuse":
            record = approvals.refuse(req_id, note)
        else:
            record = approvals.revise(req_id, note)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="申请不存在或已处理") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job_id:
        jobs.mark_approval(job_id, record)
    return record


@app.get("/v1/events", dependencies=[Depends(_bearer)])
def job_events() -> StreamingResponse:
    import json
    import time

    q = bus.subscribe()

    def _gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            deadline = time.time() + 15
            while True:
                try:
                    item = q.get(timeout=1)
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    deadline = time.time() + 15
                except Exception:
                    if time.time() >= deadline:
                        yield ": keepalive\n\n"
                        deadline = time.time() + 15
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/library", dependencies=[Depends(_bearer)])
def library_list() -> list[dict[str, Any]]:
    return library.list_books()


@app.post("/v1/library", dependencies=[Depends(_bearer)])
async def library_create(
    file: UploadFile = File(...),
    subject: str = Form("math"),
    grade_band: str = Form("hs"),
    category: str = Form("textbook"),
    label: str = Form(""),
    textbook_hint: str = Form(""),
    book_id: str = Form(""),
    status: str = Form("uploaded"),
    conversation_id: str = Form(""),
) -> dict[str, Any]:
    name = Path(file.filename or "book.pdf").name
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF")
    data = await file.read()
    try:
        return library.create_book(
            pdf_bytes=data,
            filename=name,
            book_id=book_id,
            label=label,
            subject=subject,
            grade_band=grade_band,
            category=category,
            textbook_hint=textbook_hint,
            status=status,
            conversation_id=conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/library/{book_id}", dependencies=[Depends(_bearer)])
def library_get(book_id: str) -> dict[str, Any]:
    row = library.get_book(book_id)
    if row is None:
        raise HTTPException(status_code=404, detail="book not found")
    return row


@app.patch("/v1/library/{book_id}", dependencies=[Depends(_bearer)])
def library_patch(book_id: str, body: LibraryPatchIn) -> dict[str, Any]:
    try:
        row = library.patch_book(book_id, **body.model_dump())
    except (ValueError, PathJailError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="book not found")
    return row


@app.delete("/v1/library/{book_id}", dependencies=[Depends(_bearer)])
def library_delete(book_id: str) -> dict[str, str]:
    if not library.delete_book(book_id):
        raise HTTPException(status_code=404, detail="book not found")
    return {"deleted": book_id}


@app.get("/v1/library/{book_id}/toc", dependencies=[Depends(_bearer)])
def library_toc(book_id: str) -> dict[str, Any]:
    if library.get_book(book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")
    return {"book_id": book_id, **library.read_toc(book_id)}


@app.post("/v1/library/{book_id}/reimport", dependencies=[Depends(_bearer)])
def library_reimport(book_id: str) -> dict[str, Any]:
    try:
        row = library.reimport(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="book not found")
    return row


@app.get("/v1/library/{book_id}/pages/{page_no}/markdown", dependencies=[Depends(_bearer)])
def library_page_md(book_id: str, page_no: int) -> dict[str, Any]:
    row = library.get_book(book_id)
    if row is None:
        raise HTTPException(status_code=404, detail="book not found")
    md = library.get_page_markdown(book_id, page_no)
    if md is None:
        raise HTTPException(status_code=404, detail="page not found")
    from app.mid_text import looks_like_skin, strip_skin
    from app.page_sync import sync_page

    if looks_like_skin(md):
        md = strip_skin(md)
        library.write_page(book_id, page_no, md, sync=False)
    surfaces.get_slot("page-body")
    sync_page(book_id, page_no)
    md = library.get_page_markdown(book_id, page_no) or md
    toc = library.read_toc(book_id)
    print_to_pdf = toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else {}
    mid = library.get_page_mid(book_id, page_no) or md
    prepared = surfaces.prepare_page(mid, print_to_pdf=print_to_pdf, book_id=book_id)
    return {
        "book_id": book_id,
        "page_no": page_no,
        "markdown": md,
        "prepared": prepared,
        "page_count": row["page_count"],
    }


@app.get("/v1/library/{book_id}/pages/{page_no}/image", dependencies=[Depends(_bearer)])
def library_page_image(book_id: str, page_no: int) -> Response:
    data = library.get_page_image(book_id, page_no)
    if not data:
        raise HTTPException(status_code=404, detail="page image not found")
    return Response(content=data, media_type="image/jpeg")


@app.post("/v1/books/{book_id}/toc-extract", dependencies=[Depends(_bearer)])
def start_book_toc_extract(book_id: str) -> dict[str, Any]:
    if library.get_book(book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")
    try:
        return tasks.start_toc_extract(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/library/{book_id}/toc-extract", dependencies=[Depends(_bearer)])
def library_toc_extract(book_id: str) -> dict[str, Any]:
    return start_book_toc_extract(book_id)


@app.get("/v1/books", dependencies=[Depends(_bearer)])
def list_books() -> dict[str, Any]:
    ensure_workspace()
    return {"files": list_textbook_files()}


@app.post("/v1/books", dependencies=[Depends(_bearer)])
async def upload_book(
    file: UploadFile = File(...),
    subject: str = Form(default="math"),
) -> dict[str, str]:
    ensure_workspace()
    name = Path(file.filename or "book.pdf").name
    if not name.lower().endswith((".pdf", ".md")):
        raise HTTPException(status_code=400, detail="only .pdf or .md")
    dest_dir = settings.textbooks_dir / (subject or "math")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(await file.read())
    return {"path": str(dest.relative_to(settings.home)), "filename": name}


@app.get("/v1/config", dependencies=[Depends(_bearer)])
def get_config() -> dict[str, Any]:
    return config_store.snapshot()


@app.put("/v1/config/mcp", dependencies=[Depends(_bearer)])
def put_mcp(body: McpIn) -> dict[str, Any]:
    try:
        return config_store.write_mcp(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/v1/config/skills/{skill_id}", dependencies=[Depends(_bearer)])
def put_skill(skill_id: str, body: ContentIn) -> dict[str, str]:
    try:
        return config_store.put_skill(skill_id, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/config/skills/{skill_id}", dependencies=[Depends(_bearer)])
def delete_skill(skill_id: str) -> dict[str, str]:
    if not config_store.delete_skill(skill_id):
        raise HTTPException(status_code=404, detail="skill not found")
    return {"id": skill_id, "deleted": "true"}


@app.put("/v1/config/rules/{rule_id}", dependencies=[Depends(_bearer)])
def put_rule(rule_id: str, body: ContentIn) -> dict[str, str]:
    try:
        return config_store.put_rule(rule_id, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/config/rules/{rule_id}", dependencies=[Depends(_bearer)])
def delete_rule(rule_id: str) -> dict[str, str]:
    if not config_store.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
    return {"id": rule_id, "deleted": "true"}


@app.put("/v1/config/tools", dependencies=[Depends(_bearer)])
def put_tools(body: ToolsIn) -> dict[str, Any]:
    return config_store.write_tools(body.model_dump())


@app.put("/v1/config/tools/{tool_id}", dependencies=[Depends(_bearer)])
def put_tool(tool_id: str, body: ToolFileIn) -> dict[str, Any]:
    try:
        return config_store.put_tool(
            tool_id,
            path=body.path,
            description=body.description,
            content=body.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/config/tools/{tool_id}", dependencies=[Depends(_bearer)])
def delete_tool(tool_id: str) -> dict[str, str]:
    if not config_store.delete_tool(tool_id):
        raise HTTPException(status_code=404, detail="tool not found")
    return {"id": tool_id, "deleted": "true"}


@app.post("/v1/config/reload", dependencies=[Depends(_bearer)])
def reload_config() -> dict[str, int]:
    from app.change import after_write

    ensure_workspace()
    return {"generation": after_write("config", "reload")}


@app.get("/v1/config/prompts", dependencies=[Depends(_bearer)])
def get_prompts() -> dict[str, Any]:
    return {"prompts": prompts_store.list_prompts()}


@app.put("/v1/config/prompts/{name}", dependencies=[Depends(_bearer)])
def put_prompt(name: str, body: ContentIn) -> dict[str, Any]:
    try:
        return prompts_store.put_prompt(name, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/config/prompts/{name}", dependencies=[Depends(_bearer)])
def delete_prompt(name: str) -> dict[str, str]:
    try:
        if not prompts_store.delete_prompt(name):
            raise HTTPException(status_code=404, detail="prompt not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": name, "deleted": "true"}


@app.put("/v1/config/hooks", dependencies=[Depends(_bearer)])
def put_hooks(body: HooksIn) -> dict[str, Any]:
    try:
        return hooks_store.write_hooks(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/v1/config/hooks/scripts/{name}", dependencies=[Depends(_bearer)])
def put_hook_script(name: str, body: ContentIn) -> dict[str, str]:
    try:
        return hooks_store.put_hook_script(name, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/config/hooks/scripts/{name}", dependencies=[Depends(_bearer)])
def delete_hook_script(name: str) -> dict[str, str]:
    try:
        if not hooks_store.delete_hook_script(name):
            raise HTTPException(status_code=404, detail="script not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": name, "deleted": "true"}


@app.get("/v1/config/plugins", dependencies=[Depends(_bearer)])
def get_plugins() -> dict[str, Any]:
    return {"plugins": plugins_store.list_plugins(), "allowlist": plugins_store.read_allowlist()}


@app.post("/v1/config/plugins/upload", dependencies=[Depends(_bearer)])
async def upload_plugin(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    try:
        return plugins_store.install_zip(file.filename or "plugin.zip", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/config/plugins/git", dependencies=[Depends(_bearer)])
def install_plugin_git(body: PluginGitIn) -> dict[str, Any]:
    try:
        return plugins_store.install_git(body.url, body.ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/config/plugins/{plugin_id}/enabled", dependencies=[Depends(_bearer)])
def enable_plugin(plugin_id: str, body: PluginEnabledIn) -> dict[str, Any]:
    try:
        return plugins_store.set_enabled(plugin_id, body.enabled)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="plugin not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/v1/config/plugins/{plugin_id}", dependencies=[Depends(_bearer)])
def delete_plugin(plugin_id: str) -> dict[str, str]:
    try:
        if not plugins_store.remove_plugin(plugin_id):
            raise HTTPException(status_code=404, detail="plugin not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": plugin_id, "deleted": "true"}


@app.get("/v1/checkpoints", dependencies=[Depends(_bearer)])
def list_checkpoint_rows(limit: int = Query(default=40, ge=1, le=200)) -> dict[str, Any]:
    return {"checkpoints": checkpoints.list_checkpoints(limit)}


@app.post("/v1/checkpoints", dependencies=[Depends(_bearer)])
def create_checkpoint(body: CheckpointIn) -> dict[str, Any]:
    try:
        return checkpoints.snapshot(body.kind or "manual", body.note, body.job_id, body.conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/checkpoints/{ref}/diff", dependencies=[Depends(_bearer)])
def checkpoint_diff(ref: str) -> dict[str, Any]:
    try:
        return checkpoints.diff(ref)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/checkpoints/{ref}/restore", dependencies=[Depends(_bearer)])
def restore_checkpoint(ref: str) -> dict[str, Any]:
    try:
        return checkpoints.restore(ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/checkpoints/{ref}/restore-traces", dependencies=[Depends(_bearer)])
def restore_checkpoint_traces(ref: str) -> dict[str, Any]:
    try:
        return checkpoints.restore_traces(ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/checkpoints/{ref}/fork", dependencies=[Depends(_bearer)])
def fork_checkpoint(ref: str, body: ForkIn) -> dict[str, Any]:
    try:
        return checkpoints.fork(ref, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/surfaces", dependencies=[Depends(_bearer)])
def list_surface_slots() -> dict[str, Any]:
    return surfaces.list_slots()


@app.post("/v1/surfaces/reload", dependencies=[Depends(_bearer)])
def reload_surface_slots() -> dict[str, Any]:
    return surfaces.reload_surfaces()


@app.get("/v1/surfaces/{slot}/theme.css", dependencies=[Depends(_bearer)])
def surface_theme(slot: str) -> Response:
    try:
        css = surfaces.theme_css(slot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=css, media_type="text/css; charset=utf-8")


@app.get("/v1/surfaces/{slot}", dependencies=[Depends(_bearer)])
def get_surface_slot(slot: str) -> dict[str, Any]:
    try:
        return surfaces.get_slot(slot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/surfaces/{slot}/prepare", dependencies=[Depends(_bearer)])
def prepare_surface(slot: str, body: SurfacePrepareIn) -> dict[str, str]:
    if slot != "page-body":
        raise HTTPException(status_code=400, detail="only page-body prepares markdown")
    return {
        "prepared": surfaces.prepare_page(
            body.markdown,
            print_to_pdf=body.printToPdf,
            book_id=body.bookId,
        )
    }


@app.post("/v1/surfaces/{slot}/paint", dependencies=[Depends(_bearer)])
def paint_surface(slot: str, body: SurfacePaintIn) -> dict[str, str]:
    try:
        html = surfaces.paint(slot, body.payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"html": html}


def main() -> None:
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
