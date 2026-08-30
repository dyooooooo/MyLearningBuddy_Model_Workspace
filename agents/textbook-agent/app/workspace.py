from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings

_SEED = Path(__file__).resolve().parent.parent / "workspace"


def ensure_workspace() -> Path:
    home = settings.home
    home.mkdir(parents=True, exist_ok=True)
    settings.textbooks_dir.mkdir(parents=True, exist_ok=True)
    settings.cursor_dir.mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "rules").mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "skills").mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "plans").mkdir(parents=True, exist_ok=True)
    settings.tools_dir.mkdir(parents=True, exist_ok=True)
    (settings.tools_dir / "mcp").mkdir(parents=True, exist_ok=True)
    settings.traces_dir.mkdir(parents=True, exist_ok=True)
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    (settings.home / "logs").mkdir(parents=True, exist_ok=True)
    (settings.home / "prompts").mkdir(parents=True, exist_ok=True)
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    settings.memory_dir.mkdir(parents=True, exist_ok=True)
    (settings.memory_dir / "working").mkdir(parents=True, exist_ok=True)
    for name in ("pending", "granted", "refused", "revised"):
        (settings.memory_dir / "approvals" / name).mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "hooks").mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "plugins" / "local").mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "plugins" / "disabled").mkdir(parents=True, exist_ok=True)
    if _SEED.is_dir():
        _copy_if_missing(_SEED / ".cursor", settings.cursor_dir)
        _copy_if_missing(_SEED / "tools", settings.tools_dir)
        _copy_if_missing(_SEED / "prompts", settings.home / "prompts")
        _copy_if_missing(_SEED / "tasks", settings.tasks_dir)
        _copy_if_missing(_SEED / "memory", settings.memory_dir)
        seed_ignore = _SEED / ".cursorignore"
        dest_ignore = settings.home / ".cursorignore"
        if seed_ignore.is_file() and not dest_ignore.exists():
            shutil.copy2(seed_ignore, dest_ignore)
    from app.surfaces import ensure_surfaces

    ensure_surfaces()
    mcp = settings.cursor_dir / "mcp.json"
    if not mcp.exists():
        mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    manifest = settings.tools_dir / "manifest.yaml"
    if not manifest.exists():
        manifest.write_text("tools: []\n", encoding="utf-8")
    hooks = settings.cursor_dir / "hooks.json"
    if not hooks.exists():
        hooks.write_text(
            '{"version": 1, "hooks": {'
            '"preToolUse": [{"type": "command", "command": ".cursor/hooks/guard-redline.sh", "failClosed": true, "timeout": 10}],'
            '"beforeShellExecution": [{"type": "command", "command": ".cursor/hooks/guard-redline.sh", "failClosed": true, "timeout": 10}]'
            "}}\n",
            encoding="utf-8",
        )
    allow = settings.cursor_dir / "plugins" / "allowlist.json"
    if not allow.exists():
        allow.write_text("[]\n", encoding="utf-8")
    from app.prompts_store import ensure_prompt_files

    ensure_prompt_files()
    _chmod_hook_scripts()
    return home


def _chmod_hook_scripts() -> None:
    root = settings.cursor_dir / "hooks"
    if not root.is_dir():
        return
    for path in root.iterdir():
        if path.is_file():
            try:
                path.chmod(0o755)
            except OSError:
                pass


def _copy_if_missing(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dest / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def list_textbook_files() -> list[dict[str, str]]:
    root = settings.textbooks_dir
    if not root.is_dir():
        return []
    out: list[dict[str, str]] = []
    for path in sorted(list(root.rglob("*.pdf")) + list(root.rglob("*.md"))):
        if path.name == "SKILL.md":
            continue
        try:
            rel = str(path.relative_to(settings.home))
        except ValueError:
            rel = str(path)
        out.append({"path": rel, "name": path.name, "kind": path.suffix.lstrip(".")})
    return out


def has_textbook_files(hint: str = "") -> bool:
    root = settings.textbooks_dir
    if not root.is_dir():
        return False
    files = list(root.rglob("*.pdf")) + list(root.rglob("*.md"))
    files = [p for p in files if p.name != "SKILL.md"]
    if not files:
        return False
    needle = (hint or "").strip().lower()
    if not needle:
        return True
    for p in files:
        blob = f"{p.as_posix()} {p.stem}".lower()
        if needle in blob or any(part and part in blob for part in needle.replace("《", "").replace("》", "").split()):
            return True
    return True
