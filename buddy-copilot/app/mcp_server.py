"""Buddy Copilot MCP（Model Context Protocol）stdio 服务 —— 通用装配器。

v2（Cursor 式自主执行）：全部工具经此暴露给 Agent，本模块不含任何具体工具逻辑：
- 工具来自 app/tools/plugins/ 注册表（ToolSpec）；
- 档位 / 闸门 / 场景可见性由 agent-env/permissions.yaml 驱动；
- 统一中间件：占位符打回、调用预算、step 消息落库、闸门批准卡（阻塞轮询 SQLite）。

由 Agent.create 的 StdioMcpServerConfig 拉起：python -m app.mcp_server
会话上下文经环境变量注入：COPILOT_SESSION_ID / COPILOT_SESSION_KIND
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import get_settings
from .permissions import load_permissions
from .repeat_guard import RepeatGuard
from .schemas import find_placeholders
from .store import Store
from .tools.buddy_api import BuddyApiClient
from .tools.registry import ToolContext, ToolSpec, build_specs

log = logging.getLogger("copilot.mcp")

_settings = get_settings()
_SESSION_ID = os.environ.get("COPILOT_SESSION_ID", "")
_SESSION_KIND = os.environ.get("COPILOT_SESSION_KIND", "")
_store = Store(_settings.db_path)
_perms = load_permissions(_settings.agent_env_dir)
_ctx = ToolContext(
    settings=_settings,
    store=_store,
    client=BuddyApiClient(_settings),
    session_id=_SESSION_ID,
    kind=_SESSION_KIND,
)

mcp = FastMCP("buddy-copilot")
_guard = RepeatGuard()
_local_calls = {"n": 0}


def _dump(obj: Any, limit: int = 60000) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"...(截断，共{len(s)}字符)"


def _digest(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        s = v if isinstance(v, (int, float, bool, type(None))) else str(v)
        if isinstance(s, str) and len(s) > limit:
            s = s[:limit] + "…"
        out[k] = s
    return out


def _say(role: str, content: dict[str, Any]) -> None:
    if _SESSION_ID:
        try:
            _store.add_message(_SESSION_ID, role, content)
        except Exception:  # noqa: BLE001 - 消息落库失败不影响工具执行
            log.warning("add_message failed", exc_info=True)


def _bump_tool_calls() -> int:
    """会话级工具调用计数（落库）。无会话上下文时退回进程内计数。"""
    if not _SESSION_ID:
        _local_calls["n"] += 1
        return _local_calls["n"]
    try:
        return _store.bump_counter(_SESSION_ID, "tool_calls")
    except Exception:  # noqa: BLE001 - 计数失败不该挡住工具执行
        log.warning("bump tool_calls failed", exc_info=True)
        _local_calls["n"] += 1
        return _local_calls["n"]


def _session_cancelled() -> bool:
    if not _SESSION_ID:
        return False
    s = _store.get_session(_SESSION_ID)
    return bool(s and s.get("status") == "cancelled")


def _scope_paths(argmap: dict[str, Any]) -> list[str]:
    """批准范围 = 本次调用的 ``paths`` 参数原文。

    ``args_digest`` 会把列表转成截断字符串，事后无法可靠还原；fs_guard 的
    「路径求交」必须拿到未截断的原始清单，所以单独存一份。
    """
    raw = argmap.get("paths")
    if isinstance(raw, (list, tuple)):
        return [str(p) for p in raw]
    return []


def _gate_digest(
    spec: ToolSpec, argmap: dict[str, Any], digest: dict[str, Any]
) -> dict[str, Any]:
    """批准卡参数 = 入参摘要 + 该工具的闸门前自检结果（若声明了 preflight）。

    自检本身失败也照样进卡片：让人看见「没验成」，而不是悄悄按通过处理。
    """
    if spec.preflight is None:
        return digest
    try:
        extra = spec.preflight(argmap)
    except Exception as e:  # noqa: BLE001 - 自检不阻断闸门，结果如实呈现
        extra = {"闸门前自检": f"执行失败：{str(e)[:500]}"}
    return {**digest, **extra} if extra else digest


def _gate(
    spec: ToolSpec,
    risk: str,
    note: str,
    args_digest: dict[str, Any],
    scope_paths: list[str] | None = None,
) -> tuple[bool, str]:
    """闸门：创建批准卡（元数据下发给前端渲染），阻塞轮询直到用户决定 / 超时 / 会话取消。

    用户此前对同一动作选过「本会话总是允许」时直接放行（会话级 override）。
    """
    if _SESSION_ID and _store.has_session_allow(_SESSION_ID, spec.name):
        _say(
            "system",
            {"text": f"「{spec.title}」已按你的选择在本会话内自动放行（总是允许）。"},
        )
        return True, "（本会话总是允许）"
    timeout = _perms.budgets.gate_timeout_s
    aid = _store.create_approval(
        _SESSION_ID,
        spec.name,
        {
            "tool": spec.name,
            "title": spec.title,
            "description": note,
            "risk": risk,
            "args": args_digest,
            "scope_paths": list(scope_paths or []),
        },
    )
    _say(
        "approval",
        {
            "approval_id": aid,
            "action_key": spec.name,
            "title": spec.title,
            "description": note,
            "risk": risk,
            "args": args_digest,
        },
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        a = _store.get_approval(aid)
        if a and a["status"] != "pending":
            approved = a["status"] == "approved"
            _say(
                "approval_resolved",
                {"approval_id": aid, "status": "approved" if approved else "rejected"},
            )
            return approved, str(a.get("note") or "")
        if _session_cancelled():
            _store.decide_approval(aid, False, "会话已取消")
            _say("approval_resolved", {"approval_id": aid, "status": "cancelled"})
            return False, "会话已取消"
        time.sleep(2)
    _store.decide_approval(aid, False, "等待超时")
    _say("approval_resolved", {"approval_id": aid, "status": "expired"})
    return False, f"批准等待超时（{timeout}s），按拒绝处理"


def _wrap(spec: ToolSpec, tier: str, gated: bool, risk: str, gate_note: str):
    """统一中间件：占位符打回 → 预算 → step 落库 → 闸门 → 执行（异常转结果给 Agent 自愈）。"""

    @functools.wraps(spec.fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        bound = inspect.signature(spec.fn).bind(*args, **kwargs)
        bound.apply_defaults()
        argmap = dict(bound.arguments)
        digest = _digest(argmap)

        holes = find_placeholders(argmap)
        if holes:
            return _dump({
                "error": "参数含占位符，已打回: " + "; ".join(holes),
                "hint": "请先用只读工具查到真实值（或向用户确认），再重试。",
            })

        used = _bump_tool_calls()
        if used > _perms.budgets.max_tool_calls:
            return _dump({
                "error": f"工具调用已超预算（{_perms.budgets.max_tool_calls} 次，已用 {used}）",
                "hint": "请立即停止调用工具，给出最终报告 JSON（status=blocked 并说明进展）。",
            })

        if _session_cancelled():
            return _dump({"error": "会话已被用户取消，请立即停止"})

        base = {"tool": spec.name, "title": spec.title, "tier": tier, "args": digest}

        stuck = _guard.check(spec.name, argmap)
        if stuck:
            _say("step", {**base, "state": "skipped", "note": stuck})
            _store.audit(_SESSION_ID or None, "orchestrator", "repeat_guard", {"tool": spec.name})
            return _dump({"error": stuck, "hint": "换做法或按契约汇报，不要重发同一调用。"})

        _say("step", {**base, "state": "start"})
        _store.audit(_SESSION_ID or None, "agent", "tool_call", {"tool": spec.name, "args": digest})

        if gated:
            approved, user_note = _gate(
                spec, risk, gate_note, _gate_digest(spec, argmap, digest), _scope_paths(argmap)
            )
            if not approved:
                _say("step", {**base, "state": "skipped", "note": user_note})
                return _dump({
                    "approved": False,
                    "user_note": user_note,
                    "hint": "用户拒绝了该动作。请按用户理由调整方案后继续，不要原样重发。",
                })

        t0 = time.time()
        try:
            result = spec.fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 错误作为结果返回，Agent 自诊断修复
            elapsed = int((time.time() - t0) * 1000)
            _say(
                "step",
                {**base, "state": "error", "error": str(e)[:800], "elapsed_ms": elapsed},
            )
            return _dump({
                "error": str(e)[:2000],
                "hint": "工具执行失败。请阅读错误信息、诊断原因，修复后重试（同一失败最多 3 次）。",
            })
        elapsed = int((time.time() - t0) * 1000)
        _guard.observe(spec.name, argmap, result)
        # 结果摘要落库：前端步骤行可展开查看（完整结果只回给 Agent）
        _say(
            "step",
            {**base, "state": "done", "elapsed_ms": elapsed, "result": _dump(result, 1200)},
        )
        return _dump(result)

    return wrapper


def _assemble() -> None:
    specs = build_specs(_ctx)
    visible = set(_perms.visible_tools(_SESSION_KIND))
    for name in sorted(specs):
        if name not in visible:
            continue
        spec = specs[name]
        perm = _perms.tool(name)
        mcp.tool(name=spec.mcp_name, description=spec.resolved_description())(
            _wrap(spec, perm.tier, perm.gated, perm.risk, perm.gate_note)
        )


_assemble()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
