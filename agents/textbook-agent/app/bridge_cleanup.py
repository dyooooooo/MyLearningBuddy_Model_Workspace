"""cursor-sdk 桥进程的回收：供 main.py lifespan 在启动/关停时调用。

背景：cursor_sdk 虽注册了 atexit(close_default_client)，但进程被 SIGKILL /
崩溃杀死时 atexit 不会执行，桥进程树（sh 包装 → node → stdio MCP）会被 init
收养而残留（ppid=1），服务每重启一次就攒一批孤儿，白占内存。

- 启动时 kill_orphan_bridges(..., only_orphans=False)：单实例假设下，凡属于
  本工作区的桥树都是上次进程的残留，全部清掉；
- 关停时先 close_sdk_bridge 再 kill_orphan_bridges(..., only_orphans=False)：
  SDK 关桥失败（桥无响应）时兜底，保证桥树不残留。

桥树为什么会残留（运行期，同一个进程内）：启动脚本 cursor-sdk-bridge 是
``"$DIR/node" ... "$@"`` 而非 ``exec``，所以 sh 是 node 的父进程。SDK 关桥只对
Popen 直接子进程（那个 sh）发信号，node 收不到，于是 sh 一死 node 就被 init 收养
（ppid=1）继续跑。桥有响应时 node 会应优雅关闭自行退出，所以只有「桥卡住」才漏，
而那恰好就是 recover_bridge 的场景。

判据为什么不能只看 ppid=1：容器里本服务自己就是 PID 1，它 spawn 的**活**桥 sh 的
ppid 同样是 1，光看 ppid 无法区分活死。所以 PID 1 下改用「不属于当前活桥树」
（live_bridge_pids），拿不到活桥信息时宁可不杀，绝不误杀活桥。

不引入 psutil，直接读 /proc。薄拷贝自 buddy-copilot，两服务不互相 import。
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path

from app.config import settings

log = logging.getLogger("textbook.bridge")

SERVICE_ROOT = Path(__file__).resolve().parent.parent


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
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
            continue
        if not raw:
            continue
        out[pid] = (ppid, raw.replace(b"\0", b" ").decode("utf-8", "replace"))
    return out


def _descendants(procs: dict[int, tuple[int, str]], roots: list[int]) -> set[int]:
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


def live_bridge_pids() -> set[int] | None:
    """当前 SDK 正在用的桥进程 pid。

    ``None`` = 问不出来（SDK 内部结构变了），调用方必须当作「不确定」处理；
    ``set()`` = SDK 手上确实没有桥，此时所有桥进程都是残留。
    """
    try:
        from cursor_sdk import _client
    except Exception:
        log.warning("cursor_sdk._client 不可用，无法识别活桥", exc_info=True)
        return None
    if not hasattr(_client, "_DEFAULT_BRIDGE"):
        log.warning("cursor_sdk._client 没有 _DEFAULT_BRIDGE，无法识别活桥")
        return None
    bridge = getattr(_client, "_DEFAULT_BRIDGE", None)
    if bridge is None:
        return set()
    pids: set[int] = set()
    process = getattr(bridge, "process", None)
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and pid > 0:
        pids.add(pid)
    endpoint_pid = getattr(getattr(bridge, "endpoint", None), "pid", None)
    if isinstance(endpoint_pid, int) and endpoint_pid > 0:
        pids.add(endpoint_pid)
    # A bridge object with no usable pid tells us nothing; do not treat that as
    # "SDK owns no bridge" or we would kill the live tree.
    return pids or None


def _collect_orphan_bridge_tree(
    procs: dict[int, tuple[int, str]], marker: str, self_pid: int
) -> set[int]:
    """本工作区残留的桥树 + 桥先死后残留的孤儿 MCP。

    非 PID 1：孤儿 = ppid 已变成 1（父进程死了被 init 收养）。
    PID 1（容器）：活桥的 ppid 也是 1，改判「不属于活桥树」；活桥问不出来就不动。
    """
    as_init = self_pid == 1
    live = live_bridge_pids()
    if as_init and live is None:
        log.warning("本进程是 PID 1 且识别不出活桥，跳过孤儿清理以免误杀活桥")
        return set()
    spared = _descendants(procs, sorted(live)) if live else set()

    def _leftover(pid: int, ppid: int) -> bool:
        if pid in spared:
            return False
        return True if as_init else ppid == 1

    roots = [
        pid
        for pid, (ppid, cmd) in procs.items()
        if "cursor-sdk-bridge" in cmd and marker in cmd and _leftover(pid, ppid)
    ]
    victims = _descendants(procs, roots)
    for pid, (ppid, cmd) in procs.items():
        # The MCP sidecar is spawned by the bridge, never by us, so a ppid of 1
        # always means its bridge died — true even when we are PID 1.
        if "-m app.mcp_server" in cmd and marker in cmd and ppid == 1 and pid not in spared:
            victims.add(pid)
    victims -= spared
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

    - only_orphans=True：只杀 ppid=1 的桥树，以及桥先死后残留的 app.mcp_server；
    - only_orphans=False：杀全部匹配桥树（启动/关停时本服务单实例，皆属残留）。
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
        SERVICE_ROOT, settings.home, only_orphans=True, grace_s=grace_s
    )


def close_sdk_bridge(timeout: float = 5) -> bool:
    """关闭 SDK 默认客户端（释放桥连接），供重连/关停使用。

    返回是否在 ``timeout`` 内关完。SDK 的优雅关闭会先 HTTP 请求桥自己退出，桥卡住
    时那一步要耗掉整整 60 秒（httpx 默认超时），而这正是重连路径最常见的场景——不能
    让一次桥故障把任务顶死一分钟。所以放到守护线程里等 ``timeout``，超时就返回 False，
    让调用方直接去按 pid 强杀；那个线程留在后台跑完不影响，强杀重复了也无害。
    """
    try:
        from cursor_sdk import close_default_client
    except Exception:
        log.warning("cursor_sdk.close_default_client 不可用", exc_info=True)
        return False

    def _close() -> None:
        try:
            close_default_client()
        except Exception:
            log.warning("close_sdk_bridge: close_default_client 失败", exc_info=True)

    worker = threading.Thread(target=_close, name="close-sdk-bridge", daemon=True)
    worker.start()
    worker.join(max(0.1, float(timeout)))
    if worker.is_alive():
        log.warning("close_sdk_bridge: 桥没在 %.1fs 内关完，交给强杀兜底", timeout)
        return False
    log.info("close_sdk_bridge: close_default_client 已完成")
    return True
