"""Workspace surface slots: load, golden-gate, Node prepare/paint.

The host image owns this loader. Slot files live under AGENT_HOME/surfaces/
(bind-mounted). Broken workspace code never goes live: goldens must pass,
otherwise last-good or the image seed is used. proposals/ is never executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.paths import PathJailError, resolve_in_home

SLOTS = ("page-body", "toc-tree", "compare")
PROPOSALS = "proposals"
LAST_GOOD = ".last-good"
SEED = Path(__file__).resolve().parent.parent / "workspace" / "surfaces"
RUNNER = Path(__file__).resolve().parent / "surface_runner.mjs"
NODE_TIMEOUT_S = 8.0
CSS_MAX_CHARS = 200_000

_IMPORT_RE = re.compile(r"@import\b[^;]*;", re.I)
_BAD_URL_RE = re.compile(
    r"url\s*\(\s*['\"]?\s*(?:https?:|//|javascript:|data:)",
    re.I,
)
_STYLE_BREAK_RE = re.compile(r"</style", re.I)


@dataclass
class GoldenRow:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SlotState:
    slot: str
    source: str = "seed"
    loaded: bool = False
    profile: dict[str, Any] = field(default_factory=dict)
    css: str = ""
    goldens: list[GoldenRow] = field(default_factory=list)
    error: str = ""


_state: dict[str, SlotState] = {}
_prepare_cache: dict[tuple[str, str, str], str] = {}


def reset_for_tests() -> None:
    _state.clear()
    _prepare_cache.clear()


def surfaces_dir() -> Path:
    return settings.home / "surfaces"


def last_good_dir() -> Path:
    return surfaces_dir() / LAST_GOOD


def seed_slot(slot: str) -> Path:
    return SEED / slot


def workspace_slot(slot: str) -> Path:
    return surfaces_dir() / slot


def last_good_slot(slot: str) -> Path:
    return last_good_dir() / slot


def ensure_surfaces() -> Path:
    root = surfaces_dir()
    root.mkdir(parents=True, exist_ok=True)
    last_good_dir().mkdir(parents=True, exist_ok=True)
    if SEED.is_dir():
        # 与 tools/prompts/configs 等六个目录共用 workspace 的哈希台账：没人动过的
        # 文件随镜像升级，动过的一律保留。surfaces 早先自带一份「存在即跳过」的
        # 播种，那会让运行态永远停在第一次启动那版，改完镜像也不生效。
        from app.workspace import seed_missing

        seed_missing(
            [(seed_slot(slot), workspace_slot(slot)) for slot in SLOTS],
            skip_dirs=(PROPOSALS,),
        )
        for slot in SLOTS:
            (workspace_slot(slot) / PROPOSALS).mkdir(parents=True, exist_ok=True)
    return root


def _copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def _ignore(directory: str, names: list[str]) -> set[str]:
        if Path(directory).name == PROPOSALS or PROPOSALS in Path(directory).parts:
            return set(names)
        return {PROPOSALS} if PROPOSALS in names else set()

    shutil.copytree(src, dest, ignore=_ignore)


def _safe_slot(slot: str) -> str:
    name = (slot or "").strip()
    if name not in SLOTS:
        raise ValueError(f"unknown slot: {slot}")
    return name


def resolve_surface_path(slot: str, rel: str) -> Path:
    sid = _safe_slot(slot)
    text = (rel or "").replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("~") or ".." in text.split("/"):
        raise PathJailError("invalid surface path")
    text = text.lstrip("./")
    if text == LAST_GOOD or text.startswith(f"{LAST_GOOD}/"):
        raise PathJailError("cannot write last-good")
    dest = (workspace_slot(sid) / text).resolve()
    root = workspace_slot(sid).resolve()
    if dest != root and root not in dest.parents:
        raise PathJailError("path outside slot")
    resolve_in_home(dest)
    return dest


def sanitize_css(text: str) -> str:
    raw = (text or "")[:CSS_MAX_CHARS]
    raw = _IMPORT_RE.sub("", raw)
    raw = _BAD_URL_RE.sub("url(about:blank", raw)
    raw = _STYLE_BREAK_RE.sub("&lt;/style", raw)
    return raw


def _read_profile(folder: Path) -> dict[str, Any]:
    path = folder / "profile.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _read_css(folder: Path, slot: str = "") -> str:
    candidates = [folder / "theme.css"]
    if slot:
        for extra in (workspace_slot(slot) / "theme.css", seed_slot(slot) / "theme.css"):
            if extra not in candidates:
                candidates.append(extra)
    for path in candidates:
        if path.is_file():
            return sanitize_css(path.read_text(encoding="utf-8"))
    return ""


def _node_bin() -> str:
    return shutil.which("node") or ""


def _run_js(folder: Path, op: str, *, markdown: str = "", payload: Any = None, ctx: dict[str, Any] | None = None) -> Any:
    node = _node_bin()
    if not node:
        raise RuntimeError("node not found")
    if not RUNNER.is_file():
        raise RuntimeError("surface runner missing")
    entry = {"prepare": "prepare.mjs", "paint": "paint.mjs", "tag": "tag.mjs"}.get(op, "")
    if not entry or not (folder / entry).is_file():
        raise RuntimeError(f"missing {entry or op}")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(folder),
        "NODE_OPTIONS": "--no-warnings",
        "LANG": "C.UTF-8",
    }
    body = {
        "op": op,
        "cwd": str(folder.resolve()),
        "entry": entry,
        "markdown": markdown,
        "payload": payload if payload is not None else {},
        "ctx": ctx or {},
    }
    proc = subprocess.run(
        [node, str(RUNNER)],
        input=json.dumps(body, ensure_ascii=False),
        cwd=str(folder),
        capture_output=True,
        text=True,
        timeout=NODE_TIMEOUT_S,
        env=env,
        check=False,
    )
    stderr = (proc.stderr or "").strip()
    if stderr:
        try:
            from app.logsetup import event

            event("surface.node_stderr", op=op, slot=str(folder.name), text=stderr[:2000])
        except Exception:
            pass
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        raise RuntimeError((proc.stderr or proc.stdout or "node failed")[:2000])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"node output is not JSON: {proc.stdout[:200]}") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError(str((data or {}).get("error") or "surface failed")[:2000])
    logs = data.get("logs") if isinstance(data.get("logs"), list) else []
    if logs:
        try:
            from app.logsetup import event

            event("surface.node_logs", op=op, slot=str(folder.name), logs=logs[:40])
        except Exception:
            pass
    return data.get("result")


def _golden_pairs(folder: Path) -> list[tuple[Path, Path]]:
    root = folder / "tests" / "goldens"
    if not root.is_dir():
        return []
    pairs: list[tuple[Path, Path]] = []
    for src in sorted(root.glob("*.in.md")) + sorted(root.glob("*.in.json")):
        stem = src.name[: -len(".in.md")] if src.name.endswith(".in.md") else src.name[: -len(".in.json")]
        if src.suffix == ".md":
            dest = root / f"{stem}.out.md"
        else:
            dest = root / f"{stem}.out.html"
        if dest.is_file():
            pairs.append((src, dest))
    return pairs


def _ctx_for(src: Path, profile: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    ctx_path = src.with_name(src.name.replace(".in.md", ".ctx.json").replace(".in.json", ".ctx.json"))
    if ctx_path.is_file():
        try:
            loaded = json.loads(ctx_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                extra = loaded
        except json.JSONDecodeError:
            extra = {}
    ctx = {"profile": profile}
    try:
        from app.toc_lexicon import export_for_js

        ctx["lexicon"] = export_for_js()
    except Exception:
        pass
    ctx.update(extra)
    return ctx


def _diff_snip(expected: str, actual: str) -> str:
    if expected == actual:
        return ""
    left = expected[:180].replace("\n", "\\n")
    right = actual[:180].replace("\n", "\\n")
    return f"expected {left!r} got {right!r}"


def test_folder(slot: str, folder: Path) -> list[GoldenRow]:
    profile = _read_profile(folder)
    pairs = _golden_pairs(folder)
    if not pairs:
        return [GoldenRow(name="(none)", ok=False, detail="no goldens")]
    rows: list[GoldenRow] = []
    op = "prepare" if (folder / "prepare.mjs").is_file() else "paint"
    for src, dest in pairs:
        name = src.name
        expected = dest.read_text(encoding="utf-8")
        ctx = _ctx_for(src, profile)
        try:
            if op == "prepare":
                actual = str(_run_js(folder, "prepare", markdown=src.read_text(encoding="utf-8"), ctx=ctx) or "")
            else:
                payload = json.loads(src.read_text(encoding="utf-8") or "{}")
                actual = str(_run_js(folder, "paint", payload=payload, ctx=ctx) or "")
        except Exception as exc:  # noqa: BLE001 — gate must never raise to caller
            rows.append(GoldenRow(name=name, ok=False, detail=str(exc)[:300]))
            continue
        ok = actual == expected
        rows.append(GoldenRow(name=name, ok=ok, detail="" if ok else _diff_snip(expected, actual)))
    return rows


def _all_green(rows: list[GoldenRow]) -> bool:
    return bool(rows) and all(r.ok for r in rows)


def _pick_source(slot: str) -> tuple[Path, str, list[GoldenRow]]:
    work = workspace_slot(slot)
    work_rows = test_folder(slot, work) if work.is_dir() else [GoldenRow(name="workspace", ok=False, detail="missing")]
    if _all_green(work_rows):
        try:
            _copy_tree(work, last_good_slot(slot))
            return last_good_slot(slot), "workspace", work_rows
        except OSError:
            return work, "workspace", work_rows
    good = last_good_slot(slot)
    if good.is_dir() and ((good / "prepare.mjs").is_file() or (good / "paint.mjs").is_file()):
        return good, "last-good", work_rows
    seed = seed_slot(slot)
    return seed, "seed", work_rows


def _activate(slot: str, *, force: bool = False) -> SlotState:
    ensure_surfaces()
    current = _state.get(slot)
    if current and not force:
        return current
    folder, source, rows = _pick_source(slot)
    loaded = _all_green(rows) or source != "workspace"
    # seed/last-good are trusted copies; still require node + entry
    entry_ok = (folder / "prepare.mjs").is_file() or (folder / "paint.mjs").is_file()
    if source != "workspace":
        loaded = entry_ok and bool(_node_bin())
    state = SlotState(
        slot=slot,
        source=source,
        loaded=loaded,
        profile=_read_profile(folder),
        css=_read_css(folder, slot),
        goldens=rows,
        error="" if loaded else (next((r.detail for r in rows if not r.ok), "") or "not loaded"),
    )
    _state[slot] = state
    _prepare_cache.clear()
    return state


def reload_all() -> dict[str, Any]:
    ensure_surfaces()
    out = []
    for slot in SLOTS:
        out.append(slot_status(_activate(slot, force=True)))
    from app import bus, config_store

    bus.publish("surfaces", generation=config_store.generation(), slots=list(SLOTS))
    return {"slots": out}


def _ensure_slot(slot: str) -> SlotState:
    sid = _safe_slot(slot)
    if sid not in _state:
        return _activate(sid)
    return _state[sid]


def slot_dir(slot: str) -> Path:
    state = _ensure_slot(slot)
    if state.source == "seed":
        return seed_slot(slot)
    good = last_good_slot(slot)
    if good.is_dir():
        return good
    if state.source == "workspace":
        return workspace_slot(slot)
    return seed_slot(slot)


def slot_status(state: SlotState) -> dict[str, Any]:
    return {
        "slot": state.slot,
        "loaded": state.loaded,
        "source": state.source,
        "error": state.error,
        "profile": state.profile,
        "goldens": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in state.goldens],
        "passed": sum(1 for r in state.goldens if r.ok),
        "failed": sum(1 for r in state.goldens if not r.ok),
    }


def list_slots() -> dict[str, Any]:
    ensure_surfaces()
    return {"slots": [slot_status(_ensure_slot(s)) for s in SLOTS]}


def get_slot(slot: str) -> dict[str, Any]:
    state = _ensure_slot(slot)
    row = slot_status(state)
    row["css"] = state.css
    return row


def theme_css(slot: str) -> str:
    return _ensure_slot(slot).css


def tag_page(
    markdown: str,
    *,
    print_to_pdf: dict[str, Any] | None = None,
    book_id: str = "",
    center_texts: list[str] | None = None,
) -> str:
    state = _ensure_slot("page-body")
    raw = markdown or ""
    if not state.loaded:
        return raw
    try:
        result = _run_js(
            slot_dir("page-body"),
            "tag",
            markdown=raw,
            ctx={
                "profile": state.profile,
                "printToPdf": print_to_pdf or {},
                "bookId": book_id,
                "centerTexts": list(center_texts or []),
                "lexicon": _lexicon_for_js(),
            },
        )
        return str(result if result is not None else raw)
    except Exception:
        return raw


def _lexicon_for_js() -> dict[str, Any]:
    try:
        from app.toc_lexicon import export_for_js

        return export_for_js()
    except Exception:
        return {}


def _lexicon_stamp() -> str:
    try:
        from app.toc_lexicon import lexicon_stamp

        return lexicon_stamp()
    except Exception:
        return ""


def _entries_sha(toc_entries: Any) -> str:
    if not toc_entries:
        return ""
    blob = json.dumps(toc_entries, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def prepare_page(
    markdown: str,
    *,
    print_to_pdf: dict[str, Any] | None = None,
    book_id: str = "",
    toc_entries: list[dict[str, Any]] | None = None,
    fig_widths: dict[str, float] | None = None,
    fig_rows: list[list[str]] | None = None,
) -> str:
    state = _ensure_slot("page-body")
    raw = markdown or ""
    if not state.loaded:
        return raw
    entries = toc_entries if isinstance(toc_entries, list) else []
    widths = fig_widths if isinstance(fig_widths, dict) else {}
    rows = [list(row) for row in fig_rows] if isinstance(fig_rows, list) else []
    key = (
        state.source,
        raw,
        json.dumps(
            {
                "p": print_to_pdf or {},
                "b": book_id,
                "pr": state.profile,
                "e": _entries_sha(entries),
                "lx": _lexicon_stamp(),
                "fw": widths,
                "fr": rows,
            },
            sort_keys=True,
            ensure_ascii=False,
        ),
    )
    cached = _prepare_cache.get(key)
    started = time.monotonic()
    if cached is not None:
        try:
            from app.logsetup import event

            event(
                "surface.prepare",
                book_id=book_id,
                cache=True,
                entries=len(entries),
                ms=0,
            )
        except Exception:
            pass
        return cached
    try:
        result = _run_js(
            slot_dir("page-body"),
            "prepare",
            markdown=raw,
            ctx={
                "profile": state.profile,
                "printToPdf": print_to_pdf or {},
                "bookId": book_id,
                "tocEntries": entries,
                "lexicon": _lexicon_for_js(),
                "figWidths": widths,
                "figRows": rows,
            },
        )
        text = str(result if result is not None else raw)
    except Exception:
        text = raw
    _prepare_cache[key] = text
    try:
        from app.logsetup import event

        event(
            "surface.prepare",
            book_id=book_id,
            cache=False,
            entries=len(entries),
            ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:
        pass
    return text


def paint(slot: str, payload: dict[str, Any] | None = None) -> str:
    sid = _safe_slot(slot)
    state = _ensure_slot(sid)
    if not state.loaded:
        return ""
    try:
        result = _run_js(slot_dir(sid), "paint", payload=payload or {}, ctx={"profile": state.profile})
        return str(result if result is not None else "")
    except Exception:
        return ""


def _live_agent_home() -> bool:
    name = settings.home.resolve().name
    return name in {"agent", "agent-home"}


def write_surface(slot: str, path: str, content: str) -> dict[str, Any]:
    dest = resolve_surface_path(slot, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content if content is not None else "", encoding="utf-8")
    seed_ok = _mirror_surface_seed(slot, path, content if content is not None else "")
    return {"slot": slot, "path": path, "bytes": len(content or ""), "seed": seed_ok}


def _mirror_surface_seed(slot: str, rel: str, content: str) -> bool:
    if not _live_agent_home():
        return False
    try:
        dest = (SEED / slot / rel).resolve()
        root = (SEED / slot).resolve()
        if dest != root and root not in dest.parents:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def test_surface(slot: str = "") -> dict[str, Any]:
    ensure_surfaces()
    names = [_safe_slot(slot)] if slot else list(SLOTS)
    rows = []
    for name in names:
        folder = workspace_slot(name)
        goldens = test_folder(name, folder)
        rows.append(
            {
                "slot": name,
                "ok": _all_green(goldens),
                "goldens": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in goldens],
            }
        )
    return {"ok": all(r["ok"] for r in rows), "slots": rows}


def reload_surfaces() -> dict[str, Any]:
    return reload_all()
