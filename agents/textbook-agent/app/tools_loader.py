from __future__ import annotations

import importlib.util
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.config_store import read_tools
from app.manage_tools import BUILTINS
from app.paths import PathJailError, resolve_in_home

logger = logging.getLogger(__name__)


def _ensure_tools_on_path() -> None:
    # workspace tools/ 优先，镜像 seed 兜底；与 app/workspace_module.py 共用一套解析
    from app.workspace_module import ensure_on_path

    ensure_on_path()


def load_tool_specs() -> list[dict[str, Any]]:
    """Load Python tools listed in tools/manifest.yaml. No Cursor SDK import."""
    _ensure_tools_on_path()
    specs: list[dict[str, Any]] = []
    for item in read_tools().get("tools") or []:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or "").strip()
        rel = str(item.get("path") or "").strip()
        if not tool_id or not rel:
            continue
        if item.get("retired"):
            continue
        path = (settings.tools_dir / rel).resolve()
        if not path.is_file() or settings.tools_dir.resolve() not in path.parents:
            continue
        try:
            module = _load_module(tool_id, path)
        except Exception:
            logger.exception("skip tool %s (%s)", tool_id, path)
            continue
        fn = getattr(module, tool_id, None)
        if not callable(fn):
            continue
        specs.append(
            {
                "id": tool_id,
                "description": str(item.get("description") or tool_id),
                "execute": _wrap(fn, tool_id),
                "input_schema": _schema_for(fn),
            }
        )
    seen = {str(s.get("id") or "") for s in specs}
    for fn in BUILTINS:
        name = fn.__name__
        if name in seen:
            continue
        specs.append(
            {
                "id": name,
                "description": (fn.__doc__ or name).strip(),
                "execute": _wrap(fn, name),
                "input_schema": _schema_for(fn),
            }
        )
    return specs


def _load_module(name: str, path: Path) -> Any:
    # tools/<id>.py 走统一加载器，保证与 app shim 共用同一个模块实例
    if name.isidentifier() and path.name == f"{name}.py":
        from app.workspace_module import load

        return load(name)
    key = f"agent_tool_{name}"
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load tool {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _wrap(fn: Callable[..., Any], tool_id: str = "") -> Callable[..., Any]:
    import inspect

    names = set(inspect.signature(fn).parameters)

    def execute(args: dict[str, Any], _context: Any = None) -> Any:
        payload = {k: v for k, v in dict(args or {}).items() if k in names}
        if "root" in names:
            try:
                payload["root"] = str(resolve_in_home(payload.get("root") or settings.textbooks_dir))
            except PathJailError as exc:
                raise PathJailError(str(exc)) from exc
        if "path" in names and payload.get("path"):
            raw = Path(str(payload["path"]))
            if raw.is_absolute() or ".." in raw.parts:
                payload["path"] = str(resolve_in_home(raw, base=settings.textbooks_dir))
        from app.context import current_job_id, live_sdk_job

        inherited = live_sdk_job()
        token = current_job_id.set(inherited) if inherited and not current_job_id.get() else None
        name = tool_id or getattr(fn, "__name__", "tool")
        book = str(payload.get("book_id") or "").strip()
        from app.logsetup import bind, current_trace, digest_obj, dump_payload, event, new_trace

        started = time.monotonic()
        event("tool.start", tool=name, args=digest_obj(payload), book_id=book)
        try:
            with bind(book_id=book, origin="tool", trace_id=current_trace() or new_trace()):
                result = fn(**payload)
            ms = int((time.monotonic() - started) * 1000)
            status = _result_status(result)
            _record_tool(name, payload, result)
            _emit_tool_success(name, payload, result)
            extra: dict[str, Any] = {}
            if isinstance(result, dict):
                for key in ("levels", "level_hist", "entries", "toc_pages", "skipped", "ok", "reason"):
                    if key in result:
                        extra[key] = result[key]
                if any(k in result for k in ("levels", "level_hist", "entries")):
                    event("toc.compute", tool=name, **extra)
            event("tool.end", tool=name, status=status, ms=ms, result=digest_obj(result), **extra)
            return result
        except Exception as exc:
            ms = int((time.monotonic() - started) * 1000)
            payload_path = dump_payload(f"{name}.error.txt", traceback.format_exc())
            event("tool.end", tool=name, status="error", ms=ms, error=str(exc)[:400], payload=payload_path)
            raise
        finally:
            if token is not None:
                current_job_id.reset(token)

    return execute


_TASK_CRUD = frozenset({"create_task", "list_tasks", "update_task", "cancel_task"})


def _result_status(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("skipped") is True:
            return "skipped"
        if result.get("ok") is False:
            return "error"
        if result.get("ok") is True:
            return "ok"
    return "ok"


def _emit_tool_success(tool_id: str, payload: dict[str, Any], result: Any = None) -> None:
    name = (tool_id or "").strip()
    if not name or name in _TASK_CRUD:
        return
    from app.context import current_job_id
    from app import jobs, tasks

    extra: dict[str, Any] = {}
    book = str(payload.get("book_id") or "").strip()
    job_id = current_job_id.get() or ""
    status = _result_status(result)
    extra["status"] = status
    if isinstance(result, dict):
        if "skipped" in result:
            extra["skipped"] = bool(result.get("skipped"))
        if "ok" in result:
            extra["ok"] = bool(result.get("ok"))
    if job_id:
        extra["chat_job_id"] = job_id
        row = jobs.get_job(job_id)
        job_payload = row.get("payload") if row and isinstance(row.get("payload"), dict) else {}
        extra["book_id"] = book or str((job_payload or {}).get("book_id") or "")
        extra["conversation_id"] = str((job_payload or {}).get("conversation_id") or "")
        if str((job_payload or {}).get("trigger") or "") == f"tool.{name}":
            return
    else:
        extra["book_id"] = book
    try:
        tasks.emit(f"tool.{name}", extra)
    except Exception:
        logger.warning("tool emit failed tool=%s", name, exc_info=True)


def _record_tool(tool_id: str, payload: dict[str, Any], result: Any) -> None:
    from app.context import current_job_id
    from app import jobs

    job_id = current_job_id.get()
    if not job_id:
        return
    kind = "search" if tool_id == "search_md" else "open_pdf" if tool_id == "open_pdf_page" else "tool"
    title = tool_id
    if tool_id == "search_md":
        title = f"搜索「{payload.get('query') or ''}」"
        hits = result if isinstance(result, list) else []
        detail = "\n".join(
            f"{h.get('path', '')}: {h.get('snippet', '')}" for h in hits[:8] if isinstance(h, dict)
        ) or "无命中"
    elif tool_id == "open_pdf_page":
        title = f"打开 PDF 第 {payload.get('page') or 1} 页"
        detail = str((result or {}).get("path") if isinstance(result, dict) else result)
    else:
        if isinstance(result, (dict, list)):
            detail = json.dumps(result, ensure_ascii=False)[:4000]
        else:
            detail = str(result)[:800]
    jobs.append_trace(job_id, kind, title, detail)


def _schema_for(fn: Callable[..., Any]) -> dict[str, Any]:
    import inspect

    props: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if name == "root":
            continue
        anno = param.annotation
        typ = "string"
        if anno is int:
            typ = "integer"
        elif anno is bool:
            typ = "boolean"
        props[name] = {"type": typ}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema
