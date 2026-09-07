from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_SEED = Path(__file__).resolve().parent.parent / "workspace"
# 记下每个文件播种时的内容哈希，用来区分「agent 改过」与「只是旧版镜像播的」
_MANIFEST_NAME = ".seed-manifest.json"
_seeded_home: str | None = None


def reset_for_tests() -> None:
    global _seeded_home
    _seeded_home = None


def ensure_workspace() -> Path:
    global _seeded_home
    home = settings.home
    key = str(home)
    if _seeded_home == key:
        return home
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
    settings.configs_dir.mkdir(parents=True, exist_ok=True)
    (settings.memory_dir / "working").mkdir(parents=True, exist_ok=True)
    for name in ("pending", "granted", "refused", "revised"):
        (settings.memory_dir / "approvals" / name).mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "hooks").mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "plugins" / "local").mkdir(parents=True, exist_ok=True)
    (settings.cursor_dir / "plugins" / "disabled").mkdir(parents=True, exist_ok=True)
    if _SEED.is_dir():
        seed_missing(
            [
                (_SEED / ".cursor", settings.cursor_dir),
                (_SEED / "tools", settings.tools_dir),
                (_SEED / "prompts", settings.home / "prompts"),
                (_SEED / "tasks", settings.tasks_dir),
                (_SEED / "memory", settings.memory_dir),
                (_SEED / "configs", settings.configs_dir),
            ]
        )
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
    _seeded_home = key
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


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _manifest_path() -> Path:
    return settings.home / _MANIFEST_NAME


def _read_manifest() -> dict[str, str]:
    path = _manifest_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_manifest(data: dict[str, str]) -> None:
    path = _manifest_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("cannot write seed manifest %s", path, exc_info=True)


def seed_missing(
    pairs: Iterable[tuple[Path, Path]],
    *,
    skip_dirs: tuple[str, ...] = (),
) -> None:
    """播种若干 (镜像目录, 运行态目录) 对，共用同一本哈希台账。

    每个调用点自己读写台账会互相覆盖条目，所以读—播种—写这一轮必须包在一起。
    `skip_dirs` 里的目录名整棵跳过，给 surfaces 的 `proposals`（未审草稿）用。
    """
    manifest = _read_manifest()
    before = dict(manifest)
    for src, dest in pairs:
        _copy_if_missing(src, dest, manifest, skip_dirs=skip_dirs)
    if manifest != before:
        _write_manifest(manifest)


def _copy_if_missing(
    src: Path,
    dest: Path,
    manifest: dict[str, str] | None = None,
    skip_dirs: tuple[str, ...] = (),
) -> None:
    """Seed missing files; upgrade files the agent has not touched.

    `manifest` 记着上次播种时的哈希。运行态文件与它相同 = agent 没改过，镜像升级时
    可以安全覆盖（否则 workspace 会永远停在第一次启动那版）。哈希不同 = agent 改过，
    一律不动，只记一条日志 —— 教材目录规则就归 agent，覆盖它等于吞掉人家的修改。
    """
    if not src.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        if skip_dirs and any(part in skip_dirs for part in rel.parts[:-1]):
            continue
        target = dest / rel
        seed_sha = _sha(item)
        key = str(target.relative_to(settings.home)) if _under_home(target) else str(target)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            if manifest is not None:
                manifest[key] = seed_sha
            continue
        if manifest is None:
            continue
        target_sha = _sha(target)
        if target_sha == seed_sha:
            manifest[key] = seed_sha
            continue
        known = manifest.get(key)
        if known and known == target_sha:
            shutil.copy2(item, target)
            manifest[key] = seed_sha
            logger.info("seed upgraded (agent had not touched it): %s", key)
        elif known:
            logger.info("seed kept: %s differs from image (agent edit)", key)


def _under_home(path: Path) -> bool:
    try:
        path.relative_to(settings.home)
    except ValueError:
        return False
    return True


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
