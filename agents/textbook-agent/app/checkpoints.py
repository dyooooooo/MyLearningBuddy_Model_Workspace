"""Git checkpoints with a detached git-dir (not a .git in the workspace)."""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

EXCLUDE = """\
textbooks/
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
) -> dict[str, Any]:
    ensure_repo()
    _run("add", "-A")
    dirty = _run("diff", "--cached", "--quiet", check=False)
    head = _head()
    if dirty.returncode == 0 and head:
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


def restore(ref: str, *, traces: bool = False) -> dict[str, Any]:
    ensure_repo()
    sha = _resolve(ref)
    names_proc = _run("ls-tree", "-r", "--name-only", sha)
    names = [ln for ln in (names_proc.stdout or "").splitlines() if ln.strip()]
    keep = [n for n in names if not _skip_restore(n, traces=traces)]
    for i in range(0, len(keep), 80):
        batch = keep[i : i + 80]
        if batch:
            _run("checkout", sha, "--", *batch)
    row = snapshot("restore", f"restore {sha[:12]}", job_id="", conversation_id="")
    from app import bus, config_store, jobs

    jobs.cancel_active("restore")
    gen = config_store.bump_generation()
    bus.publish("checkpoint", generation=gen, action="restore", ref=sha, restore_id=row["id"])
    return {"id": sha, "restore_id": row["id"], "files": keep, "traces": traces}


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
    _run("branch", branch, sha)
    _run("symbolic-ref", "HEAD", f"refs/heads/{branch}")
    names_proc = _run("ls-tree", "-r", "--name-only", sha)
    names = [ln for ln in (names_proc.stdout or "").splitlines() if ln.strip()]
    keep = [n for n in names if not _skip_restore(n, traces=False)]
    for i in range(0, len(keep), 80):
        batch = keep[i : i + 80]
        if batch:
            _run("checkout", sha, "--", *batch)
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
    )
    return {
        "id": sha,
        "branch": branch,
        "conversation_id": str(conv["id"]),
        "title": f"分叉：{slug}",
        "snapshot": snap["id"],
    }
