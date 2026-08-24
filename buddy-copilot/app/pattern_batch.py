"""改进点抽象的分批推进：算还剩多少条、拼每一批的指令。

单轮预算是 900 秒 / 120 次工具调用 / 12 个文件，343 条不可能一轮做完，
所以由编排器按「还没挂模式的条数」驱动，一轮一批。
"""

from __future__ import annotations

from typing import Any

# 一批处理多少条改进点。与 scenarios 预取的 batch_size 默认值保持一致。
PATTERN_ABSTRACT_BATCH = 25
# 一次会话最多推几批。防止 Agent 每轮零产出时无限空转。
PATTERN_ABSTRACT_MAX_ROUNDS = 24


def unassigned_count(listing: dict[str, Any]) -> tuple[int, int]:
    """返回（还没挂模式的条数，本次列出的总条数）。"""
    items = listing.get("items") or listing.get("improvement_points") or []
    total = len(items)
    unassigned = sum(1 for r in items if isinstance(r, dict) and not (r.get("pattern_id") or ""))
    return unassigned, total


def batch_instruction(
    *,
    round_no: int,
    remaining: int,
    total: int,
    batch_size: int = PATTERN_ABSTRACT_BATCH,
) -> str:
    """第 round_no 批的执行指令。"""
    take = min(batch_size, remaining)
    done = max(0, total - remaining)
    return (
        f"第 {round_no} 批抽象：全部 {total} 条改进点里还有 {remaining} 条没挂模式"
        f"（已归属 {done} 条）。**本轮只处理前 {take} 条**，做完就收尾报进度，"
        "剩下的下一轮继续。\n\n"
        "取原料：`ip.list_ready`（只列 prep_status=ready 且未挂模式的）。"
        "从 procedure_json / concrete_json / locus_key 反推症状，"
        "**不要照抄 summary 文本** —— 那是施工记录，掺了页码和具体内容。\n\n"
        "每组走五步：归症状 → 写检测器 YAML → 配正反例 → "
        "`checks.dry_run` 加 `checks.run_examples` 自验迭代 → "
        "`patterns.bind_detector` 定档、`ip.set_pattern` 挂成员。\n"
        "正例召回不到 100% 或反例有误报，回去改 YAML 重跑；改不动就老实定 `playbook`。\n\n"
        f"预算：本轮最多写 12 个文件。已经写满就立刻收尾，不要硬撑到超限被整轮回滚。\n"
        "收尾按契约输出最终报告 JSON，`remaining` 写你估计的剩余条数。"
    )


def progress_text(*, round_no: int, remaining: int, total: int) -> str:
    done = max(0, total - remaining)
    return (
        f"🧩 抽象进度：第 {round_no} 批开始，已归属 {done}/{total} 条，"
        f"还剩 {remaining} 条没挂模式。"
    )
