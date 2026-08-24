from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.config_store import read_tools
from app.manage_tools import BUILTINS
from app.paths import PathJailError, resolve_in_home


def load_tool_specs() -> list[dict[str, Any]]:
    """Load Python tools listed in tools/manifest.yaml. No Cursor SDK import."""
    specs: list[dict[str, Any]] = []
    for item in read_tools().get("tools") or []:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or "").strip()
        rel = str(item.get("path") or "").strip()
        if not tool_id or not rel:
            continue
        path = (settings.tools_dir / rel).resolve()
        if not path.is_file() or settings.tools_dir.resolve() not in path.parents:
            continue
        module = _load_module(tool_id, path)
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
    spec = importlib.util.spec_from_file_location(f"agent_tool_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load tool {name} from {path}")
    module = importlib.util.module_from_spec(spec)
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
        result = fn(**payload)
        _record_tool(tool_id or getattr(fn, "__name__", "tool"), payload, result)
        return result

    return execute


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
