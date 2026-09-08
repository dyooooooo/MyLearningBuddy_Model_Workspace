#!/usr/bin/env python3
"""Allow workspace targets. Gate managed code writes behind a granted ticket."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ALLOW = '{"permission":"allow"}\n'
DENY = (
    '{"permission":"deny","user_message":'
    '"redline: blocked .cursor/projects, .checkpoints.git, /app, or memory/approvals"}\n'
)
DENY_MANAGED = (
    '{"permission":"deny",'
    '"user_message":"这次改动需要你在授权卡上确认",'
    '"agent_message":"写入被权限闸拦住。不要重试、不要绕道、不要改钩子。'
    "先调 request_code_edit，把四段说明写清楚：实际情况、为什么要改、"
    "具体改哪些地方、改完的影响。提交后停下，等用户在卡片上点授权。"
    '"}\n'
)
DENY_LAYERS = (
    '{"permission":"deny",'
    '"user_message":"书页三层：源 pages 只写官方 Markdown，中间 mid 只写围栏，不要写皮肤 HTML。",'
    '"agent_message":"写入被三层闸拦住。pages/*.md 禁止 class=kb-、'
    "nav/ol.kb-page-toc、::: kb-、残片（裸 :::、kb-center 行）。"
    "目录写纯文本行；围栏只写 mid/*.md。"
    "先读 .cursor/skills/page-layers/SKILL.md。"
    '"}\n'
)
_PAGE_FILE = re.compile(r"^textbooks/[^/]+/pages/\d{4}\.md$")
_MID_FILE = re.compile(r"^textbooks/[^/]+/mid/\d{4}\.md$")
_SKIN_HTML = re.compile(
    r'class="kb-|</?nav\b[^>]*kb-page-toc|</?ol\b[^>]*kb-page-toc',
    re.I,
)
_FENCE = re.compile(r"::: kb-[\w-]+")
_CLOSER = re.compile(r"^[ \t]*:::[ \t]*$", re.M)
_NAME_LINE = re.compile(
    r"^[ \t]*(?::: )?(kb-(?:center|title|subtitle|author|section(?:-center)?|"
    r"verse(?:-center)?|date|list-item|lead|solution(?:-tail)?|page-toc|"
    r"table-wrap|section-icon|drama|stage))[ \t]*$",
    re.M,
)
_NAME_TOKEN = re.compile(
    r"(?:^|(?<=\s))(kb-(?:center|title|subtitle|author|section(?:-center)?|"
    r"verse(?:-center)?|date|list-item|lead|solution(?:-tail)?|page-toc|"
    r"table-wrap|section-icon|drama|stage))(?=\s|[^\w-]|$)",
)
WRITE_TOOLS = frozenset(
    {
        "Write",
        "StrReplace",
        "write_tool",
        "Delete",
        "EditNotebook",
        "write",
        "strreplace",
        "write_hook_script",
        "write_surface",
        "write_hook",
    }
)
PATH_KEYS = (
    "file_path",
    "path",
    "target_file",
    "target_directory",
    "directory",
    "command",
    "cwd",
    "working_directory",
)
APP_TOKEN = re.compile(r"""(?:^|[\s"'`=])(/app)(?:/|[\s"'`]|$)""")
_PY_IMPORT = re.compile(r"""(?:^|[\s;|&])PYTHONPATH=/app(?:\s|$)""")
_SHELL_WRITE = re.compile(r"(>>?|tee\s+|sed\s+-i)")
_KNOWN_HOME = "/data/agent"


def _home() -> Path:
    return Path(os.environ.get("HOME") or "/data/agent")


def norm_path(raw: str, home: str | Path | None = None) -> str:
    compact = (raw or "").replace("\\", "/").strip()
    if not compact:
        return ""
    home_s = str(home if home is not None else _home()).replace("\\", "/").rstrip("/")
    if compact.startswith(home_s + "/"):
        compact = compact[len(home_s) + 1 :]
    elif compact == home_s:
        return ""
    known = _KNOWN_HOME.rstrip("/") + "/"
    if compact.startswith(known):
        compact = compact[len(known) :]
    while compact.startswith("./"):
        compact = compact[2:]
    compact = compact.lstrip("/")
    if "/" not in compact and compact.endswith(".py"):
        compact = f"tools/{compact}"
    return compact


def _log(raw: str) -> None:
    try:
        dest = _home() / "logs" / "hooks.log"
        dest.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
        dest.open("a", encoding="utf-8").write(f"{stamp} {raw}\n")
    except OSError:
        return


def targets(data: object) -> list[str]:
    found: list[str] = []
    if not isinstance(data, dict):
        return found
    blobs: list[object] = [data]
    inner = data.get("tool_input")
    if isinstance(inner, dict):
        blobs.append(inner)
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in PATH_KEYS:
            value = blob.get(key)
            if isinstance(value, str) and value.strip():
                found.append(value)
    return found


def blocked(text: str) -> bool:
    compact = text.replace("\\", "/")
    if ".cursor/projects" in compact or ".checkpoints.git" in compact:
        return True
    if "memory/approvals" in compact:
        return True
    stripped = _PY_IMPORT.sub(" ", compact)
    return bool(APP_TOKEN.search(stripped))


def _tool_name(data: dict) -> str:
    return str(data.get("tool_name") or data.get("tool") or "").strip()


def _is_tools_py(path: str) -> bool:
    rel = norm_path(path)
    return rel.startswith("tools/") and rel.endswith(".py")


def _is_hook_script(path: str) -> bool:
    return norm_path(path).startswith(".cursor/hooks/")


def _is_surface(path: str) -> bool:
    return norm_path(path).startswith("surfaces/")


def _is_managed(path: str) -> bool:
    return _is_tools_py(path) or _is_hook_script(path) or _is_surface(path)


def _managed_targets(data: dict, paths: list[str]) -> list[str]:
    name = _tool_name(data)
    inner = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    found = list(paths)
    if name.lower() == "write_hook_script":
        script = ""
        for blob in (inner, data):
            if isinstance(blob, dict):
                script = str(blob.get("name") or script)
        if script:
            found.append(f".cursor/hooks/{Path(script).name}")
    if name.lower() == "write_surface":
        slot = ""
        rel = ""
        for blob in (inner, data):
            if not isinstance(blob, dict):
                continue
            slot = str(blob.get("slot") or slot)
            rel = str(blob.get("path") or rel)
        if slot and rel:
            found.append(f"surfaces/{slot}/{rel.lstrip('/')}")
        elif slot:
            found.append(f"surfaces/{slot}")
    return [item for item in found if _is_managed(item)]


def _write_blob(data: dict) -> str:
    chunks: list[str] = []
    blobs: list[object] = [data]
    inner = data.get("tool_input")
    if isinstance(inner, dict):
        blobs.append(inner)
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in ("contents", "content", "new_string", "newString"):
            value = blob.get(key)
            if isinstance(value, str) and value:
                chunks.append(value)
    return "\n".join(chunks)


def _layer_violation(data: dict, paths: list[str]) -> bool:
    name = _tool_name(data)
    if name and name not in WRITE_TOOLS and name.lower() not in WRITE_TOOLS:
        return False
    blob = _write_blob(data)
    if not blob:
        return False
    for raw in paths:
        rel = norm_path(raw)
        if _PAGE_FILE.match(rel) and (
            _SKIN_HTML.search(blob)
            or _FENCE.search(blob)
            or _CLOSER.search(blob)
            or _NAME_LINE.search(blob)
            or _NAME_TOKEN.search(blob)
        ):
            return True
        if _MID_FILE.match(rel) and _SKIN_HTML.search(blob):
            return True
    return False


def writes_managed(data: dict, paths: list[str]) -> list[str]:
    managed = _managed_targets(data, paths)
    if not managed:
        return []
    name = _tool_name(data)
    if name and name not in WRITE_TOOLS and name.lower() not in WRITE_TOOLS:
        if name.lower() in {"shell", "bash"}:
            command = ""
            for item in paths:
                if _SHELL_WRITE.search(item):
                    command = item
                    break
            if command:
                return managed
        return []
    return managed


def _parse_when(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _consume_ticket(path: str) -> bool:
    want = norm_path(path)
    if not want:
        return False
    root = _home() / "memory" / "approvals" / "granted"
    if not root.is_dir():
        return False
    now = datetime.now(timezone.utc)
    for item in sorted(root.glob("*.json")):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        ticket_path = norm_path(str(data.get("path") or ""))
        if ticket_path != want:
            continue
        expires = _parse_when(str(data.get("expires_at") or ""))
        if expires is not None and now > expires:
            try:
                item.unlink()
            except OSError:
                pass
            continue
        try:
            item.unlink()
        except OSError:
            return False
        return True
    return False


def decide(raw: str) -> str:
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    paths = targets(data)
    for item in paths:
        if blocked(item):
            return DENY
    if _layer_violation(data, paths):
        return DENY_LAYERS
    managed = writes_managed(data, paths)
    if managed:
        for item in managed:
            if _consume_ticket(item):
                return ALLOW
        return DENY_MANAGED
    return ALLOW


def main() -> None:
    raw = sys.stdin.read()
    _log(raw)
    sys.stdout.write(decide(raw))


if __name__ == "__main__":
    main()
