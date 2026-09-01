"""Read workspace task recipes and start jobs. No cron/trigger hardcoded here."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from app import bus, jobs
from app.config import settings
from app.workspace import ensure_workspace

logger = logging.getLogger(__name__)

EVENT_IMPORT = "kb.import.completed"
EVENT_IMPORT_DROPPED = "kb.import.dropped"
MODES = frozenset({"schedule", "trigger"})
LEGACY_MODES = frozenset({"manual"})
ACTIONS = frozenset({"page_refine", "toc_extract", "chat"})
TASK_CRUD_TOOLS = frozenset({"create_task", "list_tasks", "update_task", "cancel_task"})
TRIGGER_PREFIXES = ("kb.", "task.", "tool.")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_MESSAGE = "执行任务「{{title}}」。先读工作区技能，再按对话里的要求做。"
CONDITION_SKIP = "条件不满足，跳过本任务"
IDENTIFY_MESSAGE = (
    "刚拖入一本教材 PDF。文件名：{{filename}}。书号：{{book_id}}。\n"
    "MinerU 已经识别了前三页，文本在下面。不要再 open_pdf_page，不要读 PDF，不要看图。\n"
    "文本已在对话里：第一件事就调用 propose_import_meta，不要先读 SKILL、不要 glob、不要搜索工作区。\n"
    "根据文本判断科目、学段、类型、显示名、书名提示。\n"
    "显示名和书名提示里的册号用阿拉伯数字：写「第1册」不要写「第一册」，方便按标题排序。\n"
    "科目只能是 math / chinese / physics / chemistry / biology / english，"
    "学段只能是 ms（初中）/ hs（高中）/ ps（小学）。\n"
    "类型只能是 textbook（教材）/ knowledge（知识点）/ syllabus（考纲）/ "
    "material（资料）/ homework（作业）。课程标准、命题要求、考试说明归 syllabus。\n"
    "然后只调用一次 propose_import_meta(book_id, label, textbook_hint, subject, grade_band, category)。\n"
    "不要开始识别，不要 refine_page / toc_extract / improve_render / build_toc。\n"
    "填完后用一两句话说明依据，等用户在导入页核对。"
    "用户点「开始识别」后才会导入和清洗。\n\n"
    "----- MinerU 前三页 -----\n"
    "{{preview}}"
)

_lock = threading.Lock()
_kick_lock = threading.Lock()
_emitting_lock = threading.Lock()
_emitting: set[str] = set()
_loaded_home = ""
_cache: list[dict[str, Any]] = []
_OVERLAY_KEYS = (
    "enabled",
    "title",
    "mode",
    "trigger",
    "cron",
    "action",
    "prompt",
    "message",
    "condition",
    "next_run",
    "last_run_at",
    "last_job_id",
)
# Builtin recipes live in builtin.yaml. Overlay only keeps runtime state so a
# yaml edit (e.g. insert toc_extract) is not frozen by an old tasks.json message.
_RUNTIME_OVERLAY_KEYS = (
    "enabled",
    "condition",
    "next_run",
    "last_run_at",
    "last_job_id",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def reset_for_tests() -> None:
    global _loaded_home
    with _lock:
        _cache.clear()
        _loaded_home = ""
    with _emitting_lock:
        _emitting.clear()


def tasks_dir() -> Path:
    ensure_workspace()
    path = settings.tasks_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _builtin_path() -> Path:
    return tasks_dir() / "builtin.yaml"


def _store_path() -> Path:
    return tasks_dir() / "tasks.json"


def _load_builtin() -> list[dict[str, Any]]:
    path = _builtin_path()
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("tasks builtin.yaml unreadable", exc_info=True)
        return []
    rows = data.get("tasks") if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for item in rows or []:
        if isinstance(item, dict) and item.get("id"):
            row = dict(item)
            row["builtin"] = True
            out.append(_normalize(row))
    return out


def _load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"tasks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tasks": []}
    if not isinstance(data, dict):
        return {"tasks": []}
    data.setdefault("tasks", [])
    return data


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    tid = str(row.get("id") or "").strip()
    mode = str(row.get("mode") or "trigger").strip().lower()
    if mode not in MODES and mode not in LEGACY_MODES:
        mode = "trigger"
    action = str(row.get("action") or "chat").strip().lower()
    if action not in ACTIONS:
        action = "chat"
    title = str(row.get("title") or tid or "任务")
    message = str(row.get("message") or "").strip()
    if not message and action == "page_refine":
        message = (
            "教材已导入完成。书号：{{book_id}}。"
            "请先读工作区 .cursor/skills/page-refine/SKILL.md，再对这本书做逐页清洗。"
        )
    if not message and action == "toc_extract":
        message = "书号：{{book_id}}。请抽子目录：toc_extract(book_id={{book_id}})。"
    return {
        "id": tid,
        "title": title,
        "enabled": bool(row.get("enabled", True)),
        "mode": mode,
        "trigger": str(row.get("trigger") or "").strip(),
        "cron": str(row.get("cron") or "").strip(),
        "next_run": str(row.get("next_run") or "").strip(),
        "action": action,
        "prompt": str(row.get("prompt") or "prompts/tasks.md").strip(),
        "message": message,
        "condition": str(row.get("condition") or "").strip() if mode == "trigger" else "",
        "identify_message": str(row.get("identify_message") or "").strip()
        or (IDENTIFY_MESSAGE if tid == "import-page-refine" else ""),
        "builtin": bool(row.get("builtin")),
        "last_run_at": str(row.get("last_run_at") or ""),
        "last_job_id": str(row.get("last_job_id") or ""),
    }


def _persist(rows: list[dict[str, Any]]) -> None:
    builtin_ids = {str(item.get("id") or "") for item in _load_builtin()}
    store_rows: list[dict[str, Any]] = []
    for row in rows:
        tid = str(row.get("id") or "")
        if tid in builtin_ids:
            overlay = {"id": tid}
            for key in _RUNTIME_OVERLAY_KEYS:
                if key in row:
                    overlay[key] = row[key]
            store_rows.append(overlay)
        else:
            store_rows.append(_normalize(row))
    payload = {"tasks": store_rows, "updated_at": _now_iso()}
    path = _store_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    with _lock:
        global _loaded_home
        _cache[:] = [_normalize(r) for r in rows]
        _loaded_home = str(settings.home)
    bus.publish("task", action="updated")


def _merged() -> list[dict[str, Any]]:
    global _loaded_home
    home = str(settings.home)
    with _lock:
        if _loaded_home == home and _cache:
            return [dict(r) for r in _cache]
    builtins = _load_builtin()
    store = _load_store()
    overlays = {
        str(item.get("id") or ""): item
        for item in (store.get("tasks") or [])
        if isinstance(item, dict) and item.get("id")
    }
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in builtins:
        tid = str(base["id"])
        overlay = overlays.get(tid) or {}
        row = dict(base)
        for key in _RUNTIME_OVERLAY_KEYS:
            if key in overlay:
                row[key] = overlay[key]
        row = _normalize(row)
        row["builtin"] = True
        out.append(row)
        seen.add(tid)
    for item in store.get("tasks") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        tid = str(item["id"])
        if tid in seen:
            continue
        out.append(_normalize(item))
        seen.add(tid)
    with _lock:
        _cache[:] = out
        _loaded_home = home
    return [dict(r) for r in out]


def list_events() -> list[dict[str, str]]:
    """Catalog of trigger events: system, task-completed, and local tools."""
    out: list[dict[str, str]] = [
        {"id": EVENT_IMPORT, "label": "导入教材完成后", "group": "系统"},
        {"id": EVENT_IMPORT_DROPPED, "label": "拖入 PDF 后认前三页", "group": "系统"},
    ]
    for row in list_tasks():
        tid = str(row.get("id") or "").strip()
        if not tid:
            continue
        title = str(row.get("title") or tid)
        out.append({"id": f"task.{tid}.completed", "label": f"{title}（完成）", "group": "任务完成"})
    try:
        from app.config_store import read_tools

        items = read_tools().get("tools") or []
    except Exception:
        items = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or "").strip()
        if not tid or tid in TASK_CRUD_TOOLS or tid in seen:
            continue
        seen.add(tid)
        desc = str(item.get("description") or tid).strip() or tid
        out.append({"id": f"tool.{tid}", "label": desc, "group": "工具"})
    return out


def _trigger_allowed(name: str) -> bool:
    if not name:
        return False
    if name in {e["id"] for e in list_events()}:
        return True
    return name.startswith(TRIGGER_PREFIXES)


def _check_trigger(name: str) -> str:
    name = (name or "").strip() or EVENT_IMPORT
    if not _trigger_allowed(name):
        raise ValueError(f"unknown trigger: {name}")
    return name


def list_tasks() -> list[dict[str, Any]]:
    return _merged()


def get_task(task_id: str) -> dict[str, Any] | None:
    tid = (task_id or "").strip()
    for row in _merged():
        if row.get("id") == tid:
            return row
    return None


def get_task_detail(task_id: str) -> dict[str, Any] | None:
    row = get_task(task_id)
    if row is None:
        return None
    out = dict(row)
    try:
        from app import prompts_store

        out["prompt_text"] = str(row.get("message") or "") or prompts_store.read_file(
            str(row.get("prompt") or "")
        )
    except Exception:
        out["prompt_text"] = ""
    jid = str(row.get("last_job_id") or "")
    if jid:
        job = jobs.get_job(jid)
        if job:
            out["last_job"] = {
                "job_id": job.get("job_id"),
                "kind": job.get("kind"),
                "status": job.get("status"),
                "error": job.get("error") or "",
                "updated_at": job.get("updated_at") or "",
            }
    return out


def _parse_iso(value: str) -> datetime:
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    when = datetime.fromisoformat(raw)
    if when.tzinfo is None:
        when = when.replace(tzinfo=LOCAL_TZ)
    return when.astimezone(timezone.utc)


def _next_cron(expr: str, now: datetime | None = None) -> str:
    from croniter import croniter

    when = now or _now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    naive = when.astimezone(LOCAL_TZ).replace(tzinfo=None)
    nxt = croniter(expr, naive).get_next(datetime)
    return nxt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()


def _ensure_next_run(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("mode") != "schedule" or not row.get("enabled"):
        return row
    if row.get("next_run"):
        return row
    if not row.get("cron"):
        return row
    try:
        row["next_run"] = _next_cron(str(row["cron"]))
    except Exception:
        logger.warning("bad cron for task %s: %s", row.get("id"), row.get("cron"), exc_info=True)
    return row


def _prepare_schedule(cron: str, next_run: str) -> tuple[str, str]:
    cron = (cron or "").strip()
    next_run = (next_run or "").strip()
    if cron:
        computed = _next_cron(cron)
        return cron, next_run or computed
    if next_run:
        _parse_iso(next_run)
        return "", next_run
    raise ValueError("schedule task needs a time")


def _fill_vars(text: str, values: dict[str, str]) -> str:
    for key, val in values.items():
        text = text.replace("{{" + key + "}}", val)
    return text


def _wrap_condition(condition: str, message: str) -> str:
    return (
        "先根据实际情况判断下面条件是否成立。\n"
        f"不成立则只回复「{CONDITION_SKIP}」，不要调用任何工具，不要执行后面的任务。\n"
        "成立才执行后面的任务。\n\n"
        f"条件：{condition}\n\n"
        f"任务：\n{message}"
    )


def _render_message(task: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
    extra = dict(extra or {})
    title = str(task.get("title") or "任务")
    text = str(extra.get("message") or task.get("message") or "").strip() or DEFAULT_MESSAGE
    values = {"title": title, **{str(k): "" if v is None else str(v) for k, v in extra.items()}}
    text = _fill_vars(text, values)
    if str(task.get("mode") or "") != "trigger":
        return text
    condition = _fill_vars(str(task.get("condition") or "").strip(), values)
    if not condition:
        return text
    return _wrap_condition(condition, text)


def create_task(
    title: str,
    mode: str = "trigger",
    trigger: str = "",
    cron: str = "",
    action: str = "chat",
    prompt: str = "",
    enabled: bool = True,
    next_run: str = "",
    message: str = "",
    condition: str = "",
) -> dict[str, Any]:
    mode = (mode or "trigger").strip().lower()
    if mode in LEGACY_MODES:
        raise ValueError("mode must be schedule or trigger")
    if mode not in MODES:
        raise ValueError("mode must be schedule or trigger")
    action = "chat"
    cron_out = ""
    next_out = ""
    trigger_out = ""
    if mode == "schedule":
        cron_out, next_out = _prepare_schedule(cron, next_run)
    elif mode == "trigger":
        trigger_out = _check_trigger(trigger)
    row = _normalize(
        {
            "id": str(uuid.uuid4()),
            "title": (title or "").strip() or "任务",
            "enabled": enabled,
            "mode": mode,
            "trigger": trigger_out,
            "cron": cron_out,
            "next_run": next_out,
            "action": action,
            "prompt": prompt or "prompts/tasks.md",
            "message": (message or "").strip() or DEFAULT_MESSAGE.replace("{{title}}", (title or "").strip() or "任务"),
            "condition": (condition or "").strip() if mode == "trigger" else "",
            "builtin": False,
        }
    )
    row = _ensure_next_run(row)
    rows = _merged()
    rows.append(row)
    _persist(rows)
    return row


def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    rows = _merged()
    found = None
    for row in rows:
        if row.get("id") == task_id:
            found = row
            break
    if found is None:
        return None
    for key in ("title", "enabled", "mode", "trigger", "cron", "action", "prompt", "message", "condition", "next_run"):
        if key in fields and fields[key] is not None:
            found[key] = fields[key]
    if fields.get("mode") in LEGACY_MODES:
        raise ValueError("mode must be schedule or trigger")
    found = _normalize(found)
    mode = found.get("mode")
    if mode in LEGACY_MODES:
        rows = [found if r.get("id") == task_id else r for r in rows]
        _persist(rows)
        return found
    found["action"] = "chat"
    if not str(found.get("message") or "").strip():
        found["message"] = DEFAULT_MESSAGE.replace("{{title}}", str(found.get("title") or "任务"))
    if mode == "trigger":
        found["cron"] = ""
        found["next_run"] = ""
        found["trigger"] = _check_trigger(found.get("trigger") or "")
    elif mode == "schedule":
        found["trigger"] = ""
        cron_in = found.get("cron") or ""
        next_in = found.get("next_run") or ""
        if fields.get("cron") is not None or fields.get("next_run") is not None or not next_in:
            found["cron"], found["next_run"] = _prepare_schedule(cron_in, next_in)
    rows = [found if r.get("id") == task_id else r for r in rows]
    _persist(rows)
    return found


def cancel_task(task_id: str) -> dict[str, Any]:
    row = update_task(task_id, enabled=False)
    if row is None:
        raise ValueError("task not found")
    cancelled = 0
    for job in jobs.list_jobs(200):
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if str((payload or {}).get("task_id") or "") != task_id:
            continue
        if job.get("status") in ("queued", "running"):
            jobs.request_cancel(str(job.get("job_id") or ""), "task cancelled")
            cancelled += 1
    return {"task": row, "cancelled_jobs": cancelled}


def emit(event: str, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    name = (event or "").strip()
    extra = dict(payload or {})
    extra.setdefault("trigger", name)
    if not name:
        return []
    with _emitting_lock:
        if name in _emitting:
            return []
        _emitting.add(name)
    try:
        started: list[dict[str, Any]] = []
        for row in _merged():
            if not row.get("enabled") or row.get("mode") != "trigger":
                continue
            if str(row.get("trigger") or "") != name:
                continue
            job = start_task(row, extra)
            if job:
                started.append(job)
        cids: list[str] = []
        for job in started:
            job_payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            cid = str((job_payload or {}).get("conversation_id") or "").strip()
            if cid:
                cids.append(cid)
        bus.publish("task", action="emit", event=name, started=len(started), conversation_ids=cids)
        return started
    finally:
        with _emitting_lock:
            _emitting.discard(name)


def tick() -> list[dict[str, Any]]:
    now = _now()
    started: list[dict[str, Any]] = []
    rows = _merged()
    dirty = False
    for row in rows:
        if not row.get("enabled") or row.get("mode") != "schedule":
            continue
        cron = str(row.get("cron") or "").strip()
        if not row.get("next_run"):
            if not cron:
                continue
            try:
                row["next_run"] = _next_cron(cron, now)
                dirty = True
            except Exception:
                logger.warning("bad cron for task %s", row.get("id"), exc_info=True)
            continue
        try:
            due = _parse_iso(str(row["next_run"]))
        except ValueError:
            if cron:
                row["next_run"] = _next_cron(cron, now)
                dirty = True
            continue
        if due > now:
            continue
        job = start_task(row, {})
        if job:
            started.append(job)
        if cron:
            try:
                row["next_run"] = _next_cron(cron, now)
            except Exception:
                logger.warning("bad cron for task %s", row.get("id"), exc_info=True)
        else:
            row["next_run"] = ""
            row["enabled"] = False
        dirty = True
    if dirty:
        _persist(rows)
    kick_queued_refine()
    return started


def start_task(task: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    extra = dict(extra or {})
    tid = str(task.get("id") or "")
    extra.setdefault("title", str(task.get("title") or "任务"))
    extra.setdefault("trigger", str(task.get("trigger") or ""))
    job = start_chat_task(task, extra)
    if job:
        rows = _merged()
        for row in rows:
            if row.get("id") == tid:
                row["last_run_at"] = _now_iso()
                row["last_job_id"] = str(job.get("job_id") or "")
        _persist(rows)
    return job


def _find_refine(
    book_id: str = "",
    statuses: tuple[str, ...] = ("queued", "running"),
    exclude: str = "",
) -> dict[str, Any] | None:
    want = (book_id or "").strip()
    skip = (exclude or "").strip()
    for job in jobs.list_jobs(200):
        if skip and str(job.get("job_id") or "") == skip:
            continue
        if job.get("kind") != "page_refine":
            continue
        if job.get("status") not in statuses:
            continue
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if want and str((payload or {}).get("book_id") or "") != want:
            continue
        return job
    return None


def start_page_refine(book_id: str, task_id: str = "", prompt: str = "") -> dict[str, Any]:
    from app import refine

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    existing = _find_refine(sid)
    if existing:
        return existing
    payload = {
        "book_id": sid,
        "task_id": task_id,
        "prompt": prompt or "prompts/page_refine.md",
    }
    row = jobs.create_job("page_refine", payload)
    _launch_refine(row)
    return row


def _launch_refine(row: dict[str, Any]) -> None:
    from app import refine

    job_id = str(row.get("job_id") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    book_id = str((payload or {}).get("book_id") or "")
    prompt = str((payload or {}).get("prompt") or "prompts/page_refine.md")
    if not job_id or not book_id:
        return

    def _run() -> dict:
        try:
            return refine.run_book(book_id, job_id, prompt=prompt)
        finally:
            kick_queued_refine(exclude=job_id)

    with _kick_lock:
        if _find_refine(statuses=("running",), exclude=job_id):
            return
        live = jobs.get_job(job_id)
        if not live or live.get("status") != "queued":
            return
        jobs.start_background(job_id, _run)


def _find_toc_extract(
    book_id: str = "",
    statuses: tuple[str, ...] = ("queued", "running"),
    exclude: str = "",
) -> dict[str, Any] | None:
    want = (book_id or "").strip()
    skip = (exclude or "").strip()
    for job in jobs.list_jobs(200):
        if skip and str(job.get("job_id") or "") == skip:
            continue
        if job.get("kind") != "toc_extract":
            continue
        if job.get("status") not in statuses:
            continue
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if want and str((payload or {}).get("book_id") or "") != want:
            continue
        return job
    return None


def start_toc_extract(book_id: str, task_id: str = "", prompt: str = "") -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    existing = _find_toc_extract(sid)
    if existing:
        return existing
    payload = {
        "book_id": sid,
        "task_id": task_id,
        "prompt": prompt or "prompts/toc_extract.md",
    }
    row = jobs.create_job("toc_extract", payload)
    job_id = str(row.get("job_id") or "")

    def _run() -> dict:
        from app import toc_extract

        return toc_extract.run_book(sid, job_id, prompt=str(payload["prompt"]))

    jobs.start_background(job_id, _run)
    return row


def kick_queued_refine(exclude: str = "") -> None:
    queued = _find_refine(statuses=("queued",), exclude=exclude)
    if queued is None:
        return
    _launch_refine(queued)


def start_chat_task(task: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    from app import conversations, sdk_runner
    from app.config import task_model, task_model_params

    message = _render_message(task, extra)
    cid = str(extra.get("conversation_id") or "").strip()
    conv = conversations.ensure(cid, message)
    cid = str(conv["id"])
    model = task_model()
    params = task_model_params()
    stored = {
        "message": message,
        "conversation_id": cid,
        "mode": "agent",
        "model": model,
        "model_params": params,
        "task_id": str(task.get("id") or ""),
        "trigger": str(extra.get("trigger") or task.get("trigger") or ""),
        "book_id": str(extra.get("book_id") or "").strip(),
        "attachments": [],
    }
    row = jobs.create_job("chat", stored)
    job_id = str(row["job_id"])
    conversations.attach_job(cid, job_id)
    jobs.start_background(
        job_id,
        lambda: sdk_runner.run_chat(
            message,
            cid,
            job_id=job_id,
            model=model,
            model_params=params,
        ),
    )
    return row
