"""启动清扫：把进程退出时残留的 planning/executing 会话停成可续态。

重启中断不是任务失败。标 ``failed`` 会让 Studio 右栏亮红「已失败」，
即使用户点「继续」也能跑。清扫一律停到 ``awaiting_user``。
"""

from __future__ import annotations

from collections.abc import Callable

from .store import Store

_TERMINAL = frozenset({"done", "failed", "cancelled"})
_PARKED = frozenset({"awaiting_user"})

RESTART_INTERRUPT_TEXT = (
    "服务重启导致本会话中断。"
    "点「继续」继续执行，可串接 Agent 并从断点接着跑。"
)


def sweep_zombie_sessions(
    store: Store,
    *,
    is_running: Callable[[str], bool],
    limit: int = 200,
) -> int:
    """把线程已不在、状态却停在执行中的会话停靠为 awaiting_user。

    返回清扫条数。``awaiting_user`` / 终态 / 当前仍在跑的不碰。
    """
    swept = 0
    for session in store.list_sessions(limit=limit):
        sid = str(session["id"])
        full = store.get_session(sid)
        if not full:
            continue
        status = str(full["status"] or "")
        if status in _TERMINAL or status in _PARKED or is_running(sid):
            continue
        payload = dict(full.get("payload") or {})
        payload["interrupted_by_restart"] = True
        store.update_session(sid, status="awaiting_user", payload=payload)
        store.add_message(
            sid,
            "system",
            {
                "text": RESTART_INTERRUPT_TEXT,
                "actions": [{"id": "continue", "label": "继续", "send": "继续"}],
                "restart_interrupt": True,
            },
        )
        store.audit(sid, "orchestrator", "zombie_swept", {"was": status})
        swept += 1
    return swept
