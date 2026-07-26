"""确认卡等待期间用户消息的意图分类（Cursor 式连续对话）。

官方模型：对话通道与闸门分离——用户随便问一句 ≠ 打回简报。
仅当消息明确表示「同意执行」或「打回/修改方案」时才改变确认卡状态；
其余一律按澄清问答处理：Agent 回答后确认卡仍有效。
"""

from __future__ import annotations

import re
from typing import Literal

BriefIntent = Literal["approve", "revise", "clarify"]

_APPROVE = re.compile(
    r"(确认执行|同意执行|开始执行|按简报|按这个(执行|来|做)|可以执行"
    r"|没问题.*执行|批准|开工|go\s*ahead|lgtm|(?<![a-z])ok(?![a-z])|(?<![a-z])yes(?![a-z])"
    r"|^同意$|^确认$)",
    re.I,
)

_REVISE = re.compile(
    r"(拒绝|打回|重拟|重写|重新(规划|拟|做简报)|改成|换成|不要用|别用|换方案"
    r"|修改方案|改方案|不对|错了|重新来|不要这样|别按这个)",
    re.I,
)

_QUESTION = re.compile(r"[?？]|告诉我|接下来|要执行哪些|做什么|怎么做|为什么|什么意思|哪些|解释一下")


def classify_brief_message(text: str) -> BriefIntent:
    """把确认卡等待期间的用户输入分成 approve / revise / clarify。"""
    t = (text or "").strip()
    if not t:
        return "clarify"
    if _REVISE.search(t):
        return "revise"
    # 疑问优先于短批准口令（「确认一下你要做什么？」→ clarify）
    if _QUESTION.search(t):
        return "clarify"
    if _APPROVE.search(t) and len(t) <= 40:
        return "approve"
    # 默认：Cursor 式普通跟进，不打回简报
    return "clarify"
