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
EVENT_IMPORT_CONVERTING = "kb.import.converting"
MODES = frozenset({"schedule", "trigger"})
LEGACY_MODES = frozenset({"manual"})
ACTIONS = frozenset({"page_refine", "toc_extract", "printed_toc", "chat"})
_CONVERT_POLL_S = 2.0
_CONVERT_HEARTBEAT_S = 15.0
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
    "这个工具会写回导入表并立刻入队全书识别，不要等用户点按钮，不要再调 mineru_convert。\n"
    "不要 refine_page / toc_extract / improve_render / build_toc。\n"
    "填完后用一两句话说明依据，并说明已经开始全书识别。\n\n"
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
    "when",
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
    if not message and action == "printed_toc":
        message = "书号：{{book_id}}。请核对印刷目录：resolve_printed_toc(book_id={{book_id}})。"
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
        "when": _parse_when(row.get("when")) if mode == "trigger" else {},
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
        {"id": EVENT_IMPORT_CONVERTING, "label": "开始全书识别后", "group": "系统"},
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


def _parse_when(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if str(k).strip()}
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if str(k).strip()}
    out: dict[str, Any] = {}
    for part in text.split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def _read_book_meta(book_id: str) -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        return {}
    path = settings.textbooks_dir / sid / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _when_value(key: str, meta: dict[str, Any], extra: dict[str, Any]) -> str:
    if key == "status":
        return str(extra.get("status") or "").strip()
    return str(meta.get(key) if meta.get(key) not in (None, "") else extra.get(key) or "").strip()


def _when_match(expected: Any, actual: str) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return actual in {str(item).strip() for item in expected}
    return actual == str(expected).strip()


def when_matches(when: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    extra = dict(extra or {})
    clause = when if isinstance(when, dict) else _parse_when(when)
    if not clause:
        return True, {"when": {}, "matched": True}
    book_id = str(extra.get("book_id") or "").strip()
    meta = _read_book_meta(book_id)
    evidence = {"book_id": book_id, "when": clause, "actual": {}, "matched": True}
    for key, expected in clause.items():
        actual = _when_value(str(key), meta, extra)
        evidence["actual"][str(key)] = actual
        if not _when_match(expected, actual):
            evidence["matched"] = False
            evidence["failed"] = str(key)
            return False, evidence
    return True, evidence


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
    when: Any = None,
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
            "when": _parse_when(when) if mode == "trigger" else {},
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
    for key in ("title", "enabled", "mode", "trigger", "cron", "action", "prompt", "message", "condition", "when", "next_run"):
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
        if name == EVENT_IMPORT_CONVERTING:
            book_id = str(extra.get("book_id") or "").strip()
            if book_id:
                watch = start_convert_progress(
                    book_id,
                    conversation_id=str(extra.get("conversation_id") or ""),
                    trigger=name,
                )
                if watch:
                    started.append(watch)
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
    matched, evidence = when_matches(task.get("when"), extra)
    try:
        from app.logsetup import event

        event(
            "task.gate",
            task_id=tid,
            trigger=str(extra.get("trigger") or ""),
            matched=matched,
            when=evidence.get("when"),
            actual=evidence.get("actual"),
            failed=evidence.get("failed"),
            book_id=str(extra.get("book_id") or ""),
        )
    except Exception:
        logger.warning("task.gate log failed task=%s", tid, exc_info=True)
    if not matched:
        return None
    action = str(task.get("action") or "chat").strip().lower()
    if action == "printed_toc":
        book_id = str(extra.get("book_id") or "").strip()
        if not book_id:
            return None
        job = start_printed_toc(
            book_id,
            task_id=tid,
            trigger=str(extra.get("trigger") or task.get("trigger") or ""),
            conversation_id=str(extra.get("conversation_id") or ""),
        )
    else:
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


def _find_printed_toc(
    book_id: str = "",
    statuses: tuple[str, ...] = ("queued", "running"),
    exclude: str = "",
) -> dict[str, Any] | None:
    want = (book_id or "").strip()
    skip = (exclude or "").strip()
    for job in jobs.list_jobs(200):
        if skip and str(job.get("job_id") or "") == skip:
            continue
        if job.get("kind") != "printed_toc":
            continue
        if job.get("status") not in statuses:
            continue
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if want and str((payload or {}).get("book_id") or "") != want:
            continue
        return job
    return None


def _import_conversation_id(book_id: str, extra_cid: str = "") -> str:
    from app import library

    cid = (extra_cid or "").strip()
    if cid and cid not in ("none", "local"):
        return cid
    meta = library.get_book(book_id) or {}
    cid = str(meta.get("conversation_id") or "").strip()
    if cid in ("none", "local"):
        return ""
    return cid


def _remember_conversation(book_id: str, conversation_id: str) -> None:
    from app import library

    cid = (conversation_id or "").strip()
    sid = (book_id or "").strip()
    if not sid or not cid:
        return
    meta = library.get_book(sid) or {}
    if str(meta.get("conversation_id") or "").strip() == cid:
        return
    library.patch_book(sid, conversation_id=cid)


def _count_page_files(book_id: str) -> int:
    from app import library

    folder = library.book_dir(book_id) / "pages"
    if not folder.is_dir():
        return 0
    return len(list(folder.glob("*.md")))


def _find_convert(
    book_id: str = "",
    statuses: tuple[str, ...] = ("queued", "running"),
    exclude: str = "",
) -> dict[str, Any] | None:
    want = (book_id or "").strip()
    skip = (exclude or "").strip()
    for job in jobs.list_jobs(200):
        if skip and str(job.get("job_id") or "") == skip:
            continue
        if job.get("kind") != "convert":
            continue
        if job.get("status") not in statuses:
            continue
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if want and str((payload or {}).get("book_id") or "") != want:
            continue
        return job
    return None


def start_convert_progress(
    book_id: str,
    conversation_id: str = "",
    trigger: str = "",
) -> dict[str, Any] | None:
    import time

    from app import conversations, library

    sid = (book_id or "").strip()
    if not sid:
        return None
    existing = _find_convert(sid)
    if existing:
        return existing
    cid = _import_conversation_id(sid, conversation_id)
    if not cid:
        return None
    message = f"开始识别全书。书号：{sid}。"
    conv = conversations.ensure(cid, message, focus={"book_id": sid})
    cid = str(conv.get("id") or cid)
    _remember_conversation(sid, cid)
    payload = {
        "book_id": sid,
        "trigger": trigger or EVENT_IMPORT_CONVERTING,
        "conversation_id": cid,
        "message": message,
    }
    row = jobs.create_job("convert", payload)
    job_id = str(row.get("job_id") or "")
    conversations.attach_job(cid, job_id)

    def _run() -> dict:
        jobs.append_trace(
            job_id,
            "tool",
            "全书识别",
            "MinerU 正在识别全书。页面写出来之前会一直在这里汇报。",
        )
        last = 0
        last_beat = time.monotonic()
        while True:
            if jobs.is_cancelled(job_id):
                raise jobs.JobCancelled()
            meta = library.get_book(sid)
            if meta is None:
                raise RuntimeError(f"book not found: {sid}")
            status = str(meta.get("status") or "")
            n = _count_page_files(sid)
            now = time.monotonic()
            if n > last:
                jobs.append_trace(job_id, "tool", "已出页", f"已写出 {n} 页")
                last = n
                last_beat = now
            elif now - last_beat >= _CONVERT_HEARTBEAT_S:
                detail = (
                    "还在识别全书，页面还没写出来。"
                    if n == 0
                    else f"继续写页，目前 {n} 页。"
                )
                jobs.append_trace(job_id, "tool", "全书识别", detail)
                last_beat = now
            if status == "ready":
                if n > last:
                    jobs.append_trace(job_id, "tool", "已出页", f"已写出 {n} 页")
                jobs.append_trace(job_id, "tool", "全书识别", f"全书识别完成，共 {n} 页。")
                return {
                    "conversation_id": cid,
                    "reply": f"全书识别完成，共 {n} 页。",
                    "page_count": n,
                }
            if status == "failed":
                raise RuntimeError(str(meta.get("error") or "全书识别失败"))
            if status in ("draft",):
                raise RuntimeError("book is still a draft")
            time.sleep(_CONVERT_POLL_S)

    jobs.start_background(job_id, _run)
    return row


def start_printed_toc(
    book_id: str,
    task_id: str = "",
    trigger: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    from app import conversations, library

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    existing = _find_printed_toc(sid)
    if existing:
        return existing
    meta = library.get_book(sid) or {}
    label = str(meta.get("label") or sid)[:40]
    message = f"正在看印刷目录并建骨架。书号：{sid}。"
    cid = _import_conversation_id(sid, conversation_id)
    conv = conversations.ensure(cid, message, title=label if not cid else "", focus={"book_id": sid})
    cid = str(conv.get("id") or cid)
    _remember_conversation(sid, cid)
    payload = {
        "book_id": sid,
        "task_id": task_id,
        "trigger": trigger,
        "conversation_id": cid,
        "message": message,
    }
    row = jobs.create_job("printed_toc", payload)
    job_id = str(row.get("job_id") or "")
    if cid:
        conversations.attach_job(cid, job_id)

    def _run() -> dict:
        from app import resolve_printed_toc, toc_extract
        from app.config import settings
        from app.workspace_module import ensure_on_path

        resolved = resolve_printed_toc.run_book(sid, job_id)
        ensure_on_path()
        from build_toc import build_toc

        built = build_toc(str(settings.home), sid)
        extracted = toc_extract.run_book(sid, job_id)
        out = dict(resolved) if isinstance(resolved, dict) else {}
        n = int(out.get("entry_count") or len(out.get("entries") or []) or 0)
        out["build_toc"] = built
        out["toc_extract"] = extracted
        out["conversation_id"] = cid
        out["reply"] = str(out.get("brief") or f"印刷目录已抽出 {n} 条")
        return out

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
    book_id = str(extra.get("book_id") or "").strip()
    cid = _import_conversation_id(book_id, str(extra.get("conversation_id") or ""))
    conv = conversations.ensure(cid, message, focus={"book_id": book_id} if book_id else None)
    cid = str(conv["id"])
    _remember_conversation(book_id, cid)
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
