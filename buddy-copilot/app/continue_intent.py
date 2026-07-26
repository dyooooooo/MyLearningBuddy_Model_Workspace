"""用户「继续 / 重连」意图：终态会话续跑未完成编排（Cursor Agent 式）。"""

from __future__ import annotations

import re

# 短口令或明确续跑；避免「接下来做什么」类澄清误触
_CONTINUE = re.compile(
    r"(^继续$|^接着(做|干|来|跑)?$|^重连$|^再试(一次)?$|^重试$|^resume$|^continue$"
    r"|^go\s*on$|^keep\s*going$"
    r"|^导入下一批$"
    r"|继续(执行|预览|导入|完成|跑|做|干|处理|任务)"
    r"|接着(执行|预览|导入|完成|跑|做)"
    r"|从断点|断点续|重新连接|再连一次)",
    re.I,
)

_QUESTION = re.compile(r"[?？]|告诉我|为什么|什么意思|怎么(样|做的)|解释一下")


def is_continue_request(text: str) -> bool:
    """用户是否在要求从断点续跑 / 重连 Agent。"""
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if _QUESTION.search(t) and not _CONTINUE.search(t):
        return False
    return bool(_CONTINUE.search(t))
