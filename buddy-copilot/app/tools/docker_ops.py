"""Docker Compose 操作（昂贵通道，仅编排器在用户确认后调用）。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REBUILD_SERVICES = ("buddy-api", "buddy-worker")


@dataclass
class CmdResult:
    ok: bool
    stdout: str
    stderr: str


def _run(args: list[str], cwd: Path, timeout: int = 1200) -> CmdResult:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return CmdResult(p.returncode == 0, p.stdout[-8000:], p.stderr[-8000:])
    except subprocess.TimeoutExpired:
        return CmdResult(False, "", f"timeout after {timeout}s: {' '.join(args)}")


def rebuild_buddy_services(workspace: Path, services: tuple[str, ...] = REBUILD_SERVICES) -> CmdResult:
    """重建并重启 buddy-api + buddy-worker（prompt/代码改动必须两个都重建）。"""
    c2 = workspace / "learning-buddy-c2"
    build = _run(["docker", "compose", "build", *services], cwd=c2)
    if not build.ok:
        return build
    up = _run(["docker", "compose", "up", "-d", *services], cwd=c2, timeout=300)
    if not up.ok:
        return up
    return CmdResult(True, build.stdout + "\n" + up.stdout, "")


def verify_file_in_container(
    workspace: Path, container: str, container_path: str, host_rel_path: str
) -> CmdResult:
    """重建后抽查：容器内文件与宿主机一致（host-docker-sync 规则要求）。"""
    host_file = workspace / host_rel_path
    if not host_file.is_file():
        return CmdResult(False, "", f"host file missing: {host_rel_path}")
    got = _run(["docker", "exec", container, "md5sum", container_path], cwd=workspace, timeout=30)
    if not got.ok:
        return got
    import hashlib

    want = hashlib.md5(host_file.read_bytes()).hexdigest()
    actual = got.stdout.split()[0] if got.stdout.split() else ""
    ok = want == actual
    return CmdResult(ok, f"host={want} container={actual}", "" if ok else "mismatch")


def compose_logs(workspace: Path, service: str, tail: int = 200) -> CmdResult:
    c2 = workspace / "learning-buddy-c2"
    return _run(["docker", "compose", "logs", "--no-color", "--tail", str(tail), service], cwd=c2, timeout=60)
