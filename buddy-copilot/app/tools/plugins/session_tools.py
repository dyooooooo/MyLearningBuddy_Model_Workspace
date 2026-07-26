"""会话工具插件：任务清单（Todo）卡片维护。

Agent 在执行阶段用 session.update_todos 维护任务清单；
前端把最新一条 todos 消息渲染为实时打勾的清单卡（与 Cursor 的 Todo 体验对齐）。
"""

from __future__ import annotations

import json
from typing import Any

from ..registry import ToolContext, ToolSpec, register


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    def session_update_todos(todos_json: str) -> Any:
        """更新本会话的任务清单卡（UI 实时显示）。todos_json 为 JSON 数组：
        [{"text": "步骤描述", "status": "pending|in_progress|done|skipped"}, ...]。
        整个清单整体覆盖式更新；建议开始执行时建清单，每完成一步就更新一次。"""
        try:
            items = json.loads(todos_json)
        except json.JSONDecodeError as e:
            return {"error": f"todos_json 非法: {e}"}
        if not isinstance(items, list):
            return {"error": "todos_json 须为数组"}
        norm: list[dict[str, str]] = []
        for it in items[:30]:
            if isinstance(it, str):
                norm.append({"text": it[:200], "status": "pending"})
            elif isinstance(it, dict):
                status = str(it.get("status") or "pending")
                if status not in ("pending", "in_progress", "done", "skipped"):
                    status = "pending"
                norm.append({"text": str(it.get("text") or "")[:200], "status": status})
        if ctx.session_id:
            ctx.store.add_message(ctx.session_id, "todos", {"items": norm})
        return {"ok": True, "count": len(norm)}

    return [ToolSpec("session.update_todos", "更新任务清单", session_update_todos)]
