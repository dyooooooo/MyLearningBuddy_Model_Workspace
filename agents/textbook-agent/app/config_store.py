from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.paths import PathJailError, resolve_in_home
from app.workspace import ensure_workspace

_generation = 0


def generation() -> int:
    return _generation


def bump_generation() -> int:
    global _generation
    _generation += 1
    return _generation


def _mcp_path() -> Path:
    return settings.cursor_dir / "mcp.json"


def _manifest_path() -> Path:
    return settings.tools_dir / "manifest.yaml"


def read_mcp() -> dict[str, Any]:
    ensure_workspace()
    raw = _mcp_path().read_text(encoding="utf-8")
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        return {"mcpServers": {}}
    data.setdefault("mcpServers", {})
    return data


def write_mcp(data: dict[str, Any]) -> dict[str, Any]:
    ensure_workspace()
    if "mcpServers" not in data:
        data = {"mcpServers": data}
    try:
        _assert_mcp_inside_home(data)
    except PathJailError as exc:
        raise ValueError(str(exc)) from exc
    _mcp_path().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from app.change import after_write

    after_write("config", "write mcp")
    return read_mcp()


def _assert_mcp_inside_home(data: dict[str, Any]) -> None:
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict):
        return
    for spec in servers.values():
        if not isinstance(spec, dict):
            continue
        for key in ("cwd", "root"):
            raw = spec.get(key)
            if raw:
                resolve_in_home(str(raw))
        for arg in spec.get("args") or []:
            text = str(arg)
            if text.startswith("/"):
                resolve_in_home(text)


def list_skills() -> list[dict[str, str]]:
    ensure_workspace()
    root = settings.cursor_dir / "skills"
    out: list[dict[str, str]] = []
    if not root.is_dir():
        return out
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        md = skill_dir / "SKILL.md"
        if md.exists():
            out.append({"id": skill_dir.name, "content": md.read_text(encoding="utf-8")})
    return out


def put_skill(skill_id: str, content: str) -> dict[str, str]:
    ensure_workspace()
    sid = _safe_id(skill_id)
    path = settings.cursor_dir / "skills" / sid / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    from app.change import after_write

    after_write("config", f"write skill {sid}")
    return {"id": sid, "content": content}


def delete_skill(skill_id: str) -> bool:
    path = settings.cursor_dir / "skills" / _safe_id(skill_id) / "SKILL.md"
    if not path.exists():
        return False
    path.unlink()
    from app.change import after_write

    after_write("config", f"delete skill {skill_id}")
    return True


def list_rules() -> list[dict[str, str]]:
    ensure_workspace()
    root = settings.cursor_dir / "rules"
    out: list[dict[str, str]] = []
    if not root.is_dir():
        return out
    for md in sorted(root.glob("*.mdc")):
        out.append({"id": md.stem, "content": md.read_text(encoding="utf-8")})
    return out


def put_rule(rule_id: str, content: str) -> dict[str, str]:
    ensure_workspace()
    rid = _safe_id(rule_id)
    path = settings.cursor_dir / "rules" / f"{rid}.mdc"
    path.write_text(content, encoding="utf-8")
    from app.change import after_write

    after_write("config", f"write rule {rid}")
    return {"id": rid, "content": content}


def delete_rule(rule_id: str) -> bool:
    path = settings.cursor_dir / "rules" / f"{_safe_id(rule_id)}.mdc"
    if not path.exists():
        return False
    path.unlink()
    from app.change import after_write

    after_write("config", f"delete rule {rule_id}")
    return True


def read_tools() -> dict[str, Any]:
    ensure_workspace()
    raw = _manifest_path().read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {"tools": []}
    if not isinstance(data, dict):
        return {"tools": []}
    data.setdefault("tools", [])
    return data


def write_tools(data: dict[str, Any]) -> dict[str, Any]:
    ensure_workspace()
    if "tools" not in data:
        data = {"tools": data if isinstance(data, list) else []}
    _manifest_path().write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    from app.change import after_write

    after_write("config", "write tools")
    return read_tools()


def list_tools_detailed() -> dict[str, Any]:
    data = read_tools()
    items: list[dict[str, Any]] = []
    for item in data.get("tools") or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").strip()
        content = ""
        if rel:
            path = (settings.tools_dir / rel).resolve()
            if path.is_file() and settings.tools_dir.resolve() in path.parents:
                content = path.read_text(encoding="utf-8")
        row = dict(item)
        row["content"] = content
        items.append(row)
    return {"tools": items}


def put_tool(tool_id: str, *, path: str = "", description: str = "", content: str | None = None) -> dict[str, Any]:
    ensure_workspace()
    sid = _safe_id(tool_id)
    rel = (path or f"{sid}.py").replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise ValueError("invalid path")
    dest = (settings.tools_dir / rel).resolve()
    if settings.tools_dir.resolve() not in dest.parents:
        raise ValueError("path outside tools")
    if content is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    data = read_tools()
    tools = [t for t in (data.get("tools") or []) if isinstance(t, dict)]
    found = False
    for item in tools:
        if str(item.get("id") or "") == sid:
            item["path"] = rel
            if description:
                item["description"] = description
            found = True
            break
    if not found:
        tools.append({"id": sid, "path": rel, "description": description or sid})
    write_tools({"tools": tools})
    return next(t for t in list_tools_detailed()["tools"] if t.get("id") == sid)


def delete_tool(tool_id: str) -> bool:
    sid = _safe_id(tool_id)
    data = read_tools()
    tools = [t for t in (data.get("tools") or []) if isinstance(t, dict)]
    kept = [t for t in tools if str(t.get("id") or "") != sid]
    if len(kept) == len(tools):
        return False
    write_tools({"tools": kept})
    return True


def snapshot() -> dict[str, Any]:
    from app import hooks_store, plugins_store, prompts_store

    return {
        "generation": generation(),
        "mcp": read_mcp(),
        "skills": list_skills(),
        "rules": list_rules(),
        "tools": list_tools_detailed(),
        "prompts": prompts_store.list_prompts(),
        "hooks": hooks_store.snapshot(),
        "plugins": plugins_store.list_plugins(),
        "plugin_allowlist": plugins_store.read_allowlist(),
    }


def _safe_id(value: str) -> str:
    cleaned = "".join(ch for ch in (value or "").strip() if ch.isalnum() or ch in "-_")
    if not cleaned:
        raise ValueError("id required")
    return cleaned
