"""Local Cursor plugins under ~/.cursor/plugins/local/."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.workspace import ensure_workspace

PLUGIN_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
MANIFESTS = (".cursor-plugin/plugin.json", ".claude-plugin/plugin.json", "plugin.json")
ZIP_MAX = 10 * 1024 * 1024
PARTS = ("skills", "rules", "hooks", "commands", "agents")


def local_dir() -> Path:
    return settings.cursor_dir / "plugins" / "local"


def disabled_dir() -> Path:
    return settings.cursor_dir / "plugins" / "disabled"


def allowlist_path() -> Path:
    return settings.cursor_dir / "plugins" / "allowlist.json"


def _find_manifest(root: Path) -> Path | None:
    for rel in MANIFESTS:
        path = root / rel
        if path.is_file():
            return path
    children = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
    if len(children) == 1:
        for rel in MANIFESTS:
            path = children[0] / rel
            if path.is_file():
                return path
    return None


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid plugin.json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("plugin.json must be an object")
    return data


def _plugin_id(data: dict[str, Any], fallback: str = "") -> str:
    raw = str(data.get("name") or data.get("id") or fallback or "").strip()
    if not PLUGIN_ID_RE.match(raw):
        raise ValueError(f"invalid plugin id: {raw or '(empty)'}")
    return raw


def _parts(root: Path) -> list[str]:
    found: list[str] = []
    for name in PARTS:
        if (root / name).exists():
            found.append(name)
    if (root / "hooks" / "hooks.json").is_file() and "hooks" not in found:
        found.append("hooks")
    if (root / ".mcp.json").is_file():
        found.append("mcp")
    return found


def _describe(root: Path, enabled: bool) -> dict[str, Any]:
    manifest = _find_manifest(root)
    data = _read_manifest(manifest) if manifest else {}
    pid = root.name
    try:
        if data:
            pid = _plugin_id(data, fallback=root.name)
    except ValueError:
        pid = root.name
    return {
        "id": pid,
        "name": str(data.get("name") or pid),
        "version": str(data.get("version") or ""),
        "description": str(data.get("description") or ""),
        "enabled": enabled,
        "parts": _parts(root),
        "path": str(root.relative_to(settings.home)) if root.exists() else "",
    }


def list_plugins() -> list[dict[str, Any]]:
    ensure_workspace()
    out: list[dict[str, Any]] = []
    if local_dir().is_dir():
        for child in sorted(p for p in local_dir().iterdir() if p.is_dir()):
            out.append(_describe(child, True))
    if disabled_dir().is_dir():
        for child in sorted(p for p in disabled_dir().iterdir() if p.is_dir()):
            out.append(_describe(child, False))
    return out


def read_allowlist() -> list[str]:
    ensure_workspace()
    path = allowlist_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    return []


def write_allowlist(prefixes: list[str]) -> list[str]:
    ensure_workspace()
    clean = [str(x).strip() for x in prefixes if str(x).strip()]
    allowlist_path().parent.mkdir(parents=True, exist_ok=True)
    allowlist_path().write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return clean


def _allowed_url(url: str) -> bool:
    text = (url or "").strip()
    for prefix in read_allowlist():
        if text.startswith(prefix):
            return True
    return False


def _install_dir(src: Path, plugin_id: str) -> Path:
    dest = local_dir() / plugin_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return dest


def install_zip(filename: str, blob: bytes) -> dict[str, Any]:
    ensure_workspace()
    if len(blob) > ZIP_MAX:
        raise ValueError(f"zip larger than {ZIP_MAX} bytes")
    tmp = Path(tempfile.mkdtemp(prefix="plugin-zip-"))
    try:
        zpath = tmp / "plugin.zip"
        zpath.write_bytes(blob)
        extract = tmp / "out"
        extract.mkdir()
        with zipfile.ZipFile(zpath) as zf:
            total = 0
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValueError("zip path traversal")
                total += int(info.file_size or 0)
                if total > ZIP_MAX:
                    raise ValueError(f"uncompressed zip larger than {ZIP_MAX} bytes")
            zf.extractall(extract)
        manifest = _find_manifest(extract)
        if manifest is None:
            raise ValueError("zip has no plugin.json")
        data = _read_manifest(manifest)
        pid = _plugin_id(data, fallback=Path(filename or "plugin").stem.lower())
        dest = _install_dir(manifest.parent.parent if manifest.parent.name in {".cursor-plugin", ".claude-plugin"} else manifest.parent, pid)
        from app.change import after_write

        after_write("plugins", f"install zip {pid}")
        return _describe(dest, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_git(url: str, ref: str = "") -> dict[str, Any]:
    ensure_workspace()
    remote = (url or "").strip()
    if not remote:
        raise ValueError("git url required")
    if not read_allowlist():
        raise ValueError("plugin allowlist is empty")
    if not _allowed_url(remote):
        raise ValueError(f"git url not on allowlist: {remote}")
    if not shutil.which("git"):
        raise ValueError("git is not installed")
    tmp = Path(tempfile.mkdtemp(prefix="plugin-git-"))
    dest_src = tmp / "repo"
    try:
        cmd = ["git", "clone", "--depth", "1"]
        if ref.strip():
            cmd.extend(["--branch", ref.strip()])
        cmd.extend([remote, str(dest_src)])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ValueError((proc.stderr or proc.stdout or "git clone failed")[:400])
        git_meta = dest_src / ".git"
        if git_meta.exists():
            shutil.rmtree(git_meta)
        manifest = _find_manifest(dest_src)
        if manifest is None:
            raise ValueError("cloned repo has no plugin.json")
        data = _read_manifest(manifest)
        root = manifest.parent.parent if manifest.parent.name in {".cursor-plugin", ".claude-plugin"} else manifest.parent
        pid = _plugin_id(data)
        dest = _install_dir(root, pid)
        from app.change import after_write

        after_write("plugins", f"install git {pid}")
        return _describe(dest, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def set_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    ensure_workspace()
    if not PLUGIN_ID_RE.match(plugin_id):
        raise ValueError(f"invalid plugin id: {plugin_id}")
    current = local_dir() / plugin_id if (local_dir() / plugin_id).is_dir() else disabled_dir() / plugin_id
    if not current.is_dir():
        raise FileNotFoundError(plugin_id)
    want_enabled = bool(enabled)
    is_enabled = current.parent.resolve() == local_dir().resolve()
    if want_enabled == is_enabled:
        return _describe(current, is_enabled)
    dest_root = local_dir() if want_enabled else disabled_dir()
    dest = dest_root / plugin_id
    dest_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(current), str(dest))
    from app.change import after_write

    after_write("plugins", f"{'enable' if enabled else 'disable'} {plugin_id}")
    return _describe(dest, want_enabled)


def remove_plugin(plugin_id: str) -> bool:
    if not PLUGIN_ID_RE.match(plugin_id or ""):
        raise ValueError(f"invalid plugin id: {plugin_id}")
    found = False
    for root in (local_dir(), disabled_dir()):
        dest = root / plugin_id
        if dest.is_dir():
            shutil.rmtree(dest)
            found = True
    if found:
        from app.change import after_write

        after_write("plugins", f"remove {plugin_id}")
    return found
