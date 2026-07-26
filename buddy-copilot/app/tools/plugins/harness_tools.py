"""验收工具插件：golden、pytest、页面前后对比。"""

from __future__ import annotations

import difflib
from typing import Any

from ..harness import run_pytest
from ..registry import ToolContext, ToolSpec, register


def _trim(obj: Any, limit: int = 2000) -> str:
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + f"...(截断，共{len(s)}字符)"


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    client = ctx.client

    def harness_run_golden(edition_id: str) -> Any:
        """跑方案 golden-lite 验收（slot 装配 + config sha 校验）。"""
        out = client.run_golden(edition_id)
        return {"ok": bool(out.get("ok", False)), "detail": _trim(out)}

    def harness_run_pytest(targets: list[str], timeout: int = 600) -> Any:
        """在 learning-buddy-api 的 .venv 里跑 pytest 子集。targets 仅限 tests/ 下相对路径。"""
        res = run_pytest(ctx.workspace, list(targets), timeout=int(timeout))
        return {"ok": res.ok, "summary": res.summary, "tail": res.output[-2000:]}

    def harness_page_diff(job_id: str, page_no: int) -> Any:
        """页面前后对比：当前页 HTML vs 会话开始时缓存的 before 快照，返回相似度。"""
        after = str(client.get_page_html(job_id, int(page_no)))
        before = str(ctx.session_payload().get("before_html") or "")
        if not before:
            return {"ok": True, "note": "无 before 快照，仅返回当前页概要", "after_len": len(after)}
        sm = difflib.SequenceMatcher(a=before, b=after)
        return {
            "ok": True,
            "similarity": round(sm.ratio(), 4),
            "before_len": len(before),
            "after_len": len(after),
        }

    return [
        ToolSpec("harness.run_golden", "golden 验收", harness_run_golden),
        ToolSpec("harness.run_pytest", "跑 pytest 子集", harness_run_pytest),
        ToolSpec("harness.page_diff", "页面前后对比", harness_page_diff),
    ]
