"""Read/write Cursor hooks.json and hook scripts under .cursor/hooks/."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.paths import PathJailError, resolve_in_home
from app.workspace import ensure_workspace

HOOK_EVENTS = frozenset(
    {
        "beforeShellExecution",
        "beforeMCPExecution",
        "beforeReadFile",
        "afterFileEdit",
        "beforeSubmitPrompt",
        "preToolUse",
        "postToolUse",
        "start",
        "stop",
        "sessionStart",
        "sessionEnd",
        "subagentStart",
        "subagentStop",
        "preCompact",
    }
)
ENTRY_TYPES = frozenset({"command", "prompt"})


def _hooks_json() -> Path:
    return settings.cursor_dir / "hooks.json"


def _scripts_dir() -> Path:
    return settings.cursor_dir / "hooks"


def read_hooks() -> dict[str, Any]:
    ensure_workspace()
    path = _hooks_json()
    if not path.exists():
        return {"version": 1, "hooks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {"version": 1, "hooks": {}}
    if not isinstance(data, dict):
        return {"version": 1, "hooks": {}}
    data.setdefault("version", 1)
    data.setdefault("hooks", {})
    return data


def _assert_command_inside(command: str) -> None:
    for tok in (command or "").split():
        if tok.startswith("/") or tok.startswith(".cursor/"):
            try:
                resolve_in_home(tok)
            except PathJailError as exc:
                raise ValueError(str(exc)) from exc


def _normalize_entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("hook entry must be an object")
    typ = str(raw.get("type") or "command").strip()
    if typ not in ENTRY_TYPES:
        raise ValueError(f"hook type must be command or prompt, got {typ}")
    entry: dict[str, Any] = {"type": typ}
    if typ == "command":
        command = str(raw.get("command") or "").strip()
        if not command:
            raise ValueError("command hook needs command")
        _assert_command_inside(command)
        entry["command"] = command
    else:
        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt hook needs prompt")
        entry["prompt"] = prompt
        if raw.get("model"):
            entry["model"] = str(raw.get("model"))
    if raw.get("matcher"):
        entry["matcher"] = str(raw.get("matcher"))
    if "failClosed" in raw:
        entry["failClosed"] = bool(raw.get("failClosed"))
    if raw.get("timeout") is not None:
        try:
            entry["timeout"] = max(1, int(raw.get("timeout")))
        except (TypeError, ValueError) as exc:
            raise ValueError("hook timeout must be an integer") from exc
    return entry


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    version = data.get("version", 1)
    if version != 1:
        raise ValueError("hooks.json version must be 1")
    hooks = data.get("hooks") or {}
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    clean: dict[str, list[dict[str, Any]]] = {}
    for event, entries in hooks.items():
        if event not in HOOK_EVENTS:
            raise ValueError(f"unknown hook event: {event}")
        if not isinstance(entries, list):
            raise ValueError(f"{event} entries must be a list")
        clean[str(event)] = [_normalize_entry(item) for item in entries]
    return {"version": 1, "hooks": clean}


def write_hooks(data: dict[str, Any]) -> dict[str, Any]:
    ensure_workspace()
    clean = _validate(data)
    path = _hooks_json()
    if path.exists():
        shutil.copy2(path, settings.cursor_dir / "hooks.json.bak")
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from app.change import after_write

    after_write("hooks", "write hooks.json")
    return read_hooks()


def add_entry(event: str, entry: dict[str, Any]) -> dict[str, Any]:
    if event not in HOOK_EVENTS:
        raise ValueError(f"unknown hook event: {event}")
    data = read_hooks()
    hooks = data.setdefault("hooks", {})
    rows = list(hooks.get(event) or [])
    rows.append(_normalize_entry(entry))
    hooks[event] = rows
    return write_hooks(data)


def list_hook_scripts() -> list[dict[str, str]]:
    ensure_workspace()
    root = _scripts_dir()
    out: list[dict[str, str]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name.endswith(".bak"):
            continue
        out.append({"id": path.name, "content": path.read_text(encoding="utf-8")})
    return out


def put_hook_script(name: str, content: str) -> dict[str, str]:
    ensure_workspace()
    sid = _safe_script(name)
    dest = (_scripts_dir() / sid).resolve()
    if settings.cursor_dir.resolve() not in dest.parents:
        raise ValueError("path outside hooks")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content if content is not None else "", encoding="utf-8")
    dest.chmod(0o755)
    from app.change import after_write

    after_write("hooks", f"write hook script {sid}")
    return {"id": sid, "content": dest.read_text(encoding="utf-8")}


def delete_hook_script(name: str) -> bool:
    dest = _scripts_dir() / _safe_script(name)
    if not dest.is_file():
        return False
    dest.unlink()
    from app.change import after_write

    after_write("hooks", f"delete hook script {dest.name}")
    return True


def snapshot() -> dict[str, Any]:
    data = read_hooks()
    data["scripts"] = list_hook_scripts()
    return data


def _safe_script(name: str) -> str:
    cleaned = "".join(ch for ch in (name or "").strip() if ch.isalnum() or ch in "-_.")
    if not cleaned or cleaned.startswith("."):
        raise ValueError("script name required")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError("script name must be a file name")
    return cleaned
