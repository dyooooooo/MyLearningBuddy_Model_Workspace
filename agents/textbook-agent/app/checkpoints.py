"""Git checkpoints with a detached git-dir (not a .git in the workspace)."""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Page text under textbooks/ is what the agent edits, so it has to be in the
# snapshot for a rewind to mean anything. Only the page scans and source PDFs
# are excluded; they are ~99% of the bytes and the agent never rewrites them.
EXCLUDE = """\
textbooks/**/*.jpg
textbooks/**/*.jpeg
textbooks/**/*.png
textbooks/**/*.webp
textbooks/**/*.pdf
.cursor/projects/
logs/
.title-scratch/
surfaces/.last-good/
node_modules/
.checkpoints.git/
__pycache__/
.venv_tmp/
*.libs/
"""

RESTORE_SKIP = (".cursor/projects/",)
SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")
TRAILER = re.compile(r"^(job_id|conversation_id):\s*(\S+)\s*$")


def git_dir() -> Path:
    return settings.home / ".checkpoints.git"


def work_tree() -> Path:
    return settings.home


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", f"--git-dir={git_dir()}", f"--work-tree={work_tree()}", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        cwd=str(work_tree()),
    )


def ensure_repo() -> None:
    gd = git_dir()
    if not (gd / "HEAD").exists():
        work_tree().mkdir(parents=True, exist_ok=True)
        gd.mkdir(parents=True, exist_ok=True)
        _run("init")
        _run("config", "user.email", "textbook-agent@local")
        _run("config", "user.name", "textbook-agent")
    exclude = gd / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(EXCLUDE, encoding="utf-8")


def _head() -> str:
    proc = _run("rev-parse", "HEAD", check=False)
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def snapshot_turn(job_id: str, conversation_id: str = "", note: str = "") -> str:
    """Snapshot workspace at the start of a turn and bind the SHA to the job."""
    try:
        cp = snapshot(
            "turn",
            (note or "对话开始")[:80],
            job_id=job_id,
            conversation_id=conversation_id,
        )
        sha = str(cp.get("id") or "")
        if job_id and sha:
            from app import jobs

            jobs.bind_checkpoint(job_id, sha)
        return sha
    except Exception:
        logger.warning("turn checkpoint failed", exc_info=True)
        return ""


def backfill_missing_jobs() -> dict[str, Any]:
    """Attach the current HEAD to completed turns that have no checkpoint."""
    from app import jobs

    missing = [
        row
        for row in jobs.list_jobs(200)
        if row.get("kind") in ("chat", "grade") and not jobs.job_checkpoint(row)
    ]
    if not missing:
        return {"attached": 0, "id": _head()}
    snap = snapshot("backfill", "existing turns")
    sha = str(snap.get("id") or "")
    for row in missing:
        jobs.bind_checkpoint(str(row.get("job_id") or ""), sha)
    return {"attached": len(missing), "id": sha}


def snapshot(
    kind: str = "manual",
    note: str = "",
    job_id: str = "",
    conversation_id: str = "",
    force: bool = False,
) -> dict[str, Any]:
    ensure_repo()
    _run("add", "-A")
    dirty = _run("diff", "--cached", "--quiet", check=False)
    head = _head()
    if dirty.returncode == 0 and head and not force:
        return {"id": head, "kind": kind, "note": note, "unchanged": True}
    subject = f"{kind}: {(note or kind).strip()[:80]}"
    body = []
    if job_id:
        body.append(f"job_id: {job_id}")
    if conversation_id:
        body.append(f"conversation_id: {conversation_id}")
    message = subject if not body else subject + "\n\n" + "\n".join(body)
    _run("commit", "-m", message, "--allow-empty")
    sha = _head()
    return {"id": sha, "kind": kind, "note": note or kind, "unchanged": False}


def list_checkpoints(limit: int = 40) -> list[dict[str, Any]]:
    ensure_repo()
    if not _head():
        return []
    n = max(1, min(int(limit or 40), 200))
    proc = _run("log", f"-{n}", "--format=%H%x09%cI%x09%s%x09%b%x1e", check=False)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    out: list[dict[str, Any]] = []
    for chunk in (proc.stdout or "").split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        head = lines[0].split("\t")
        if len(head) < 3:
            continue
        sha, when, subject = head[0], head[1], head[2]
        kind, _, note = subject.partition(": ")
        job_id = ""
        conversation_id = ""
        files: list[str] = []
        for line in lines[1:]:
            m = TRAILER.match(line.strip())
            if m and m.group(1) == "job_id":
                job_id = m.group(2)
            elif m and m.group(1) == "conversation_id":
                conversation_id = m.group(2)
        stat = _run("diff-tree", "--no-commit-id", "--name-only", "-r", sha, check=False)
        if stat.returncode == 0:
            files = [ln for ln in (stat.stdout or "").splitlines() if ln.strip()]
        out.append(
            {
                "id": sha,
                "time": when,
                "kind": kind or "manual",
                "note": note or subject,
                "job_id": job_id,
                "conversation_id": conversation_id,
                "files": files,
                "file_count": len(files),
            }
        )
    return out


def diff(ref: str) -> dict[str, Any]:
    ensure_repo()
    sha = _resolve(ref)
    proc = _run("show", "--stat", "--format=%H%n%cI%n%s%n", sha, check=False)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"unknown checkpoint: {ref}")
    names = _run("diff-tree", "--no-commit-id", "--name-only", "-r", sha, check=False)
    return {
        "id": sha,
        "text": proc.stdout or "",
        "files": [ln for ln in (names.stdout or "").splitlines() if ln.strip()],
    }


def _resolve(ref: str) -> str:
    raw = (ref or "").strip()
    if not raw:
        raise ValueError("checkpoint ref required")
    proc = _run("rev-parse", "--verify", raw, check=False)
    if proc.returncode != 0:
        raise ValueError(f"unknown checkpoint: {ref}")
    return (proc.stdout or "").strip()


def _skip_restore(rel: str, *, traces: bool) -> bool:
    text = rel.replace("\\", "/").lstrip("./")
    if not traces and (text == "traces" or text.startswith("traces/")):
        return True
    for prefix in RESTORE_SKIP:
        if text == prefix.rstrip("/") or text.startswith(prefix):
            return True
    return False


_CHANGE_LABEL = {"M": "改回", "A": "撤掉", "D": "找回", "R": "改名", "C": "复制"}
_KEPT_LABEL = "新增，保留"


def _name_status(sha: str, *, traces: bool = False) -> list[dict[str, str]]:
    _run("add", "-A")
    # --no-renames turns a rename into D(old) + A(new); the restore then puts the
    # old path back and drops the new one instead of missing both.
    proc = _run("diff", "--name-status", "--no-renames", sha, check=False)
    rows: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = (parts[0][:1] or "M").upper()
        path = parts[-1].strip()
        if not path or _skip_restore(path, traces=traces):
            continue
        rows.append({"path": path, "change": code, "label": _CHANGE_LABEL.get(code, "变动")})
    return rows


def _tree_dirs(sha: str) -> set[str]:
    proc = _run("ls-tree", "-r", "--name-only", sha, check=False)
    out: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        rel = line.strip()
        if rel:
            out.add(str(PurePosixPath(rel).parent))
    return out


def _removable(rel: str, dirs: set[str]) -> bool:
    """Only drop an added file whose directory already existed in the target.

    Without this guard, rewinding to a checkpoint taken before a directory was
    ever tracked would delete everything in it — e.g. every page of every
    textbook for any checkpoint older than the exclude-list change, or a whole
    book imported later, which would leave its scans behind without their text.
    """
    return str(PurePosixPath(rel).parent) in dirs


def _drop_added(rels: list[str]) -> list[str]:
    root = work_tree().resolve()
    gone: list[str] = []
    for rel in rels:
        path = root / rel
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            gone.append(rel)
            continue
        except OSError:
            logger.warning("restore could not remove %s", rel)
            continue
        gone.append(rel)
        _prune_empty(path.parent, root)
    return gone


def _prune_empty(start: Path, root: Path) -> None:
    node = start
    while node != root and root in node.parents:
        try:
            next(node.iterdir())
            return
        except StopIteration:
            pass
        except OSError:
            return
        try:
            node.rmdir()
        except OSError:
            return
        node = node.parent


def _apply_tree(sha: str, *, traces: bool) -> tuple[list[dict[str, str]], set[str]]:
    """Make the work tree match ``sha``: differing files back, added files gone."""
    plan = _name_status(sha, traces=traces)
    dirs = _tree_dirs(sha)
    back = [row["path"] for row in plan if row["change"] != "A"]
    added = [row["path"] for row in plan if row["change"] == "A"]
    for i in range(0, len(back), 80):
        batch = back[i : i + 80]
        if batch:
            _run("checkout", sha, "--", *batch)
    dropped = set(_drop_added([rel for rel in added if _removable(rel, dirs)]))
    changes: list[dict[str, str]] = []
    for row in plan:
        if _skip_restore(row["path"], traces=False):
            continue
        if row["change"] == "A" and row["path"] not in dropped:
            row = {**row, "label": _KEPT_LABEL}
        changes.append(row)
    return changes, dropped


def restore(ref: str, *, traces: bool = False) -> dict[str, Any]:
    ensure_repo()
    sha = _resolve(ref)
    # A rewind deletes files added since ``sha``, and an uncommitted file has
    # nowhere else to come back from — so pin the current state first.
    safety = snapshot("pre-restore", f"before restore {sha[:12]}")
    changes, dropped = _apply_tree(sha, traces=traces)
    from app import bus, config_store, conversations, jobs

    rewind = conversations.rewind_to_checkpoint(sha)
    row = snapshot(
        "restore",
        f"restore {sha[:12]}",
        job_id=str(rewind.get("job_id") or ""),
        conversation_id=str(rewind.get("conversation_id") or ""),
    )
    jobs.cancel_active("restore")
    gen = config_store.bump_generation()
    payload = {
        "generation": gen,
        "action": "restore",
        "ref": sha,
        "restore_id": row["id"],
        "safety_id": safety["id"],
        "changes": changes,
        "file_count": len(changes),
        "conversation_id": rewind.get("conversation_id") or "",
        "job_id": rewind.get("job_id") or "",
        "message": rewind.get("message") or "",
        "removed_job_ids": rewind.get("removed_job_ids") or [],
        "kept_plan": bool(rewind.get("kept_plan")),
    }
    bus.publish("checkpoint", **payload)
    return {
        "id": sha,
        "restore_id": row["id"],
        "safety_id": safety["id"],
        "removed_paths": sorted(dropped),
        "files": [c["path"] for c in changes],
        "changes": changes,
        "file_count": len(changes),
        "traces": traces,
        **rewind,
    }


def restore_traces(ref: str) -> dict[str, Any]:
    return restore(ref, traces=True)


def fork(ref: str, name: str) -> dict[str, Any]:
    ensure_repo()
    sha = _resolve(ref)
    slug = SAFE_NAME.sub("-", (name or "").strip())[:40].strip("-") or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    branch = f"fork/{slug}"
    existing = _run("show-ref", "--verify", f"refs/heads/{branch}", check=False)
    if existing.returncode == 0:
        raise ValueError(f"fork already exists: {branch}")
    # Pin the current state on the branch we are leaving: once HEAD moves, work
    # that was never committed would only exist on the fork.
    safety = snapshot("pre-fork", f"before fork {slug}")
    _run("branch", branch, sha)
    _run("symbolic-ref", "HEAD", f"refs/heads/{branch}")
    changes, dropped = _apply_tree(sha, traces=False)
    from app import bus, config_store, conversations

    conv = conversations.ensure("", message=f"分叉 {slug}")
    conversations.set_title(str(conv["id"]), f"分叉：{slug}", source="fork")
    snap = snapshot("fork", f"fork {slug}", conversation_id=str(conv["id"]))
    gen = config_store.bump_generation()
    bus.publish(
        "checkpoint",
        generation=gen,
        action="fork",
        ref=sha,
        branch=branch,
        conversation_id=str(conv["id"]),
        snapshot=snap["id"],
        safety_id=safety["id"],
        changes=changes,
        file_count=len(changes),
    )
    return {
        "id": sha,
        "branch": branch,
        "conversation_id": str(conv["id"]),
        "title": f"分叉：{slug}",
        "snapshot": snap["id"],
        "safety_id": safety["id"],
        "removed_paths": sorted(dropped),
        "changes": changes,
        "file_count": len(changes),
    }
