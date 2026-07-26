"""空转熔断：同一动作反复重试 / 反复写盘却不生效时，拦下来让 Agent 汇报。

背景：MinerU 分段导入曾出现 `get_blocks → patch_block → rerender_page → page_html`
的盲循环——写盘看起来成功、HTML 却没动，Agent 就换个写法再试，20 页烧掉整轮
120 次工具预算。判定「已经在原地打转」是编排器的职责，不能只靠 prompt 约束。
"""

from __future__ import annotations

import json
from typing import Any

# 会改 IR 的工具：它们的返回值带 html_changed，可用来判断这次写盘有没有效果
WRITE_TOOLS = frozenset({"corpus.patch_block", "corpus.replace_page_blocks"})


def _target_key(tool: str, args: dict[str, Any]) -> str:
    """写盘目标：优先块，其次页。同一目标的连续无效写盘才算打转。"""
    block_id = str(args.get("block_id") or "").strip()
    if block_id:
        return f"{tool}:block:{block_id}"
    page_no = args.get("page_no")
    job_id = str(args.get("job_id") or "")
    return f"{tool}:page:{job_id}:{page_no}"


def _signature(tool: str, args: dict[str, Any]) -> str:
    try:
        body = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        body = str(sorted(args.items(), key=lambda kv: kv[0]))
    return f"{tool}|{body}"


class RepeatGuard:
    """按会话（每个 MCP 进程一个）统计重复调用与无效写盘。"""

    def __init__(self, *, identical_limit: int = 3, no_effect_limit: int = 2) -> None:
        self.identical_limit = identical_limit
        self.no_effect_limit = no_effect_limit
        self._seen: dict[str, int] = {}
        self._no_effect: dict[str, int] = {}

    def check(self, tool: str, args: dict[str, Any]) -> str | None:
        """执行前判定。返回拦截理由（None = 放行）。"""
        sig = _signature(tool, args)
        seen = self._seen.get(sig, 0)
        if seen >= self.identical_limit - 1:
            self._seen[sig] = seen + 1
            return (
                f"同一调用（{tool}，参数完全相同）已重复 {seen + 1} 次，已拦下。"
                "重复同样的入参不会得到不同结果：请改变做法（换字段、换工具、换页），"
                "或把这一项写进最终报告的 issues 交人工，不要继续重试。"
            )
        self._seen[sig] = seen + 1

        if tool in WRITE_TOOLS:
            key = _target_key(tool, args)
            stuck = self._no_effect.get(key, 0)
            if stuck >= self.no_effect_limit:
                return (
                    f"该目标（{key}）已连续 {stuck} 次写盘但渲染 HTML 没有变化，已拦下。"
                    "继续改同一处只会空转：请换一层解决（整页 replace_page_blocks、"
                    "或确认是渲染层限制），仍不行就写进 issues / residuals 并停手。"
                )
        return None

    def observe(self, tool: str, args: dict[str, Any], result: Any) -> None:
        """执行后记账：写盘工具的 html_changed 决定是否算「无效」。"""
        if tool not in WRITE_TOOLS or not isinstance(result, dict):
            return
        if "html_changed" not in result:
            return
        key = _target_key(tool, args)
        if result.get("html_changed"):
            self._no_effect.pop(key, None)
        else:
            self._no_effect[key] = self._no_effect.get(key, 0) + 1
