"""cursor-sdk 桥进程的回收：供 main.py lifespan 在启动/关停时调用。

背景：cursor_sdk 虽注册了 ``atexit(close_default_client)``，但进程被 SIGKILL /
崩溃杀死时 atexit 不会执行，桥进程树（sh 包装 → node → stdio MCP）会被 init
收养而残留（ppid=1），服务每重启一次就攒一批孤儿，白占内存。

- 启动时 ``kill_orphan_bridges(..., only_orphans=False)``：单实例假设下，凡属于
  本工作区的桥树都是上次进程的残留，全部清掉；
- 关停时先 ``engine.shutdown()`` 再 ``kill_orphan_bridges(..., only_orphans=False)``：
  SDK 关桥失败（桥无响应）时兜底，保证桥树不残留。

不引入 psutil，直接读 /proc。
"""

from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

from .config import SERVICE_ROOT

log = logging.getLogger("copilot.bridge")


def _iter_processes() -> dict[int, tuple[int, str]]:
    """{pid: (ppid, cmdline)}；读不到的进程（竞态退出/无权限）跳过。"""
    out: dict[int, tuple[int, str]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            stat = Path(f"/proc/{pid}/stat").read_text()
            # comm 字段可能含空格/括号，ppid 是最后一个 ")" 之后的第 2 个字段
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
            continue
        if not raw:
            continue  # 内核线程
        out[pid] = (ppid, raw.replace(b"\0", b" ").decode("utf-8", "replace"))
    return out


def _descendants(
    procs: dict[int, tuple[int, str]], roots: list[int]
) -> set[int]:
    """roots 及其全部后代（sh → node → stdio MCP 逐级展开）。"""
    victims = set(roots)
    frontier = list(roots)
    while frontier:
        children = [
            pid
            for pid, (ppid, _cmd) in procs.items()
            if ppid in frontier and pid not in victims
        ]
        victims.update(children)
        frontier = children
    return victims


def _collect_orphan_bridge_tree(
    procs: dict[int, tuple[int, str]], marker: str, self_pid: int
) -> set[int]:
    """孤儿（ppid=1，父进程已死被 init 收养）桥树 + 桥先死后残留的孤儿 MCP。纯函数，便于单测。"""
    roots = [
        pid
        for pid, (ppid, cmd) in procs.items()
        if "cursor-sdk-bridge" in cmd and marker in cmd and ppid == 1
    ]
    victims = _descendants(procs, roots)
    for pid, (ppid, cmd) in procs.items():
        if "-m app.mcp_server" in cmd and marker in cmd and ppid == 1:
            victims.add(pid)
    victims.discard(self_pid)
    return victims


def list_bridge_pids(root: Path) -> list[int]:
    """cmdline 属于 root（cursor-sdk-bridge 及其 --workspace 路径）的桥进程 pid。"""
    marker = str(root)
    procs = _iter_processes()
    return sorted(
        pid
        for pid, (_ppid, cmd) in procs.items()
        if "cursor-sdk-bridge" in cmd and marker in cmd
    )


def kill_orphan_bridges(
    service_root: Path,
    workspace_root: Path,
    *,
    only_orphans: bool,
    grace_s: float = 3,
) -> int:
    """清理属于本服务的 cursor-sdk-bridge 进程树，返回处理的进程数。

    - ``only_orphans=True``：只杀 ppid=1（父进程已死、被 init 收养）的桥树，
      以及桥先死后残留为孤儿的 app.mcp_server；
    - ``only_orphans=False``：杀全部匹配桥树（启动/关停时本服务单实例，皆属残留）。
    """
    markers = (str(service_root), str(workspace_root))
    procs = _iter_processes()

    def _ours(cmd: str) -> bool:
        return any(m in cmd for m in markers)

    if only_orphans:
        victims = set()
        for marker in markers:
            victims |= _collect_orphan_bridge_tree(procs, marker, os.getpid())
    else:
        roots = [
            pid
            for pid, (_ppid, cmd) in procs.items()
            if "cursor-sdk-bridge" in cmd and _ours(cmd)
        ]
        victims = _descendants(procs, roots)
        for pid, (_ppid, cmd) in procs.items():
            if "-m app.mcp_server" in cmd and _ours(cmd):
                victims.add(pid)
        victims.discard(os.getpid())
    if not victims:
        return 0
    for pid in sorted(victims):
        try:
            os.kill(pid, signal.SIGTERM)
            log.info("清理桥进程 pid=%d cmd=%s", pid, procs[pid][1][:120])
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.time() + grace_s
    while time.time() < deadline:
        alive = False
        for pid in sorted(victims):
            try:
                os.kill(pid, 0)
                alive = True
            except (ProcessLookupError, PermissionError):
                pass
        if not alive:
            break
        time.sleep(0.1)
    for pid in sorted(victims):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return len(victims)


def sweep_orphan_bridges(grace_s: float = 3) -> int:
    """只清孤儿桥树（ppid=1）的便捷入口；返回清理的进程数。"""
    return kill_orphan_bridges(
        SERVICE_ROOT, SERVICE_ROOT.parent, only_orphans=True, grace_s=grace_s
    )


def close_sdk_bridge(timeout: float = 3) -> None:
    """关闭 SDK 默认客户端（释放桥连接），供重连/关停使用。

    timeout 保留与调用方签名兼容；SDK 的 close_default_client 为同步无超时参数。
    """
    _ = timeout
    try:
        from cursor_sdk import close_default_client
    except Exception:  # noqa: BLE001
        log.warning("cursor_sdk.close_default_client 不可用", exc_info=True)
        return
    try:
        close_default_client()
        log.info("close_sdk_bridge: close_default_client 已调用")
    except Exception:  # noqa: BLE001
        log.warning("close_sdk_bridge: close_default_client 失败", exc_info=True)
