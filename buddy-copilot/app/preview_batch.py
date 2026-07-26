"""全书预览：多轮 Run + 三角裁决（35B 主力 / MinerU 锚 / Cursor 仅裁决分歧）。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .schemas import PagePlanEntry

# 每轮页数：三角裁决在编排器侧跑，Agent 主要写 page_plan；6 页/轮仍合适
PREVIEW_PAGES_PER_RUN = 6

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


class PagePlanPartialReport(BaseModel):
    """单批预览归类结果（非最终报告）。"""

    page_plan_partial: list[PagePlanEntry] = Field(default_factory=list)
    pages_done: str = ""
    notes: str = ""


def page_batches(page_count: int, batch_size: int = PREVIEW_PAGES_PER_RUN) -> list[tuple[int, int]]:
    """1-based inclusive ranges covering 1..page_count."""
    n = max(0, int(page_count))
    if n <= 0:
        return []
    out: list[tuple[int, int]] = []
    start = 1
    while start <= n:
        end = min(start + batch_size - 1, n)
        out.append((start, end))
        start = end + 1
    return out


def extract_page_plan_partial(text: str) -> PagePlanPartialReport | None:
    matches = _JSON_BLOCK.findall(text or "")
    if not matches:
        return None
    try:
        data = json.loads(matches[-1])
        return PagePlanPartialReport.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return None


def merge_page_plans(existing: list[dict[str, Any]], partial: list[PagePlanEntry]) -> list[dict[str, Any]]:
    """追加合并；同 pages 键后者覆盖。"""
    by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        if isinstance(item, dict) and item.get("pages"):
            by_key[str(item["pages"])] = dict(item)
    for e in partial:
        d = e.model_dump()
        by_key[str(d["pages"])] = d
    return list(by_key.values())


def batch_instruction(
    *,
    minio_key: str,
    start: int,
    end: int,
    page_count: int,
    run_index: int,
    run_total: int,
    already: list[dict[str, Any]],
    triad: dict[str, Any] | None = None,
    edition_id: str = "",
) -> str:
    done_hint = f"{len(already)} 段" if already else "尚无"
    triad = triad or {}
    escalate = triad.get("escalate_pages") or []
    escalate_images = triad.get("escalate_images") or {}
    draft_plan = triad.get("page_plan_draft") or []
    triad_compact = {
        "anchor_source": triad.get("anchor_source"),
        "escalate_pages": escalate,
        "escalate_images": escalate_images,
        "compare": triad.get("compare") or [],
        "merged": triad.get("merged") or [],
        "page_plan_draft": draft_plan,
    }
    triad_json = json.dumps(triad_compact, ensure_ascii=False, indent=2)

    escalate_block = (
        "本轮**无分歧页**——不要用文件读取打开任何预览图；直接基于 merged / page_plan_draft 产出 page_plan_partial。\n"
        if not escalate
        else (
            f"仅对分歧页 {escalate} 用 Cursor 文件读取打开下列路径做视觉裁决"
            f"（其余页禁止 Read）：\n"
            + "\n".join(f"- p{p}: {escalate_images.get(str(p), '')}" for p in escalate)
            + "\n"
        )
    )

    return (
        f"## 全书预览 · 第 {run_index}/{run_total} 轮（三角裁决）\n"
        f"- PDF：`{minio_key}`（共 {page_count} 页）\n"
        f"- 本轮页 **{start}–{end}**；已累计 page_plan 段：{done_hint}\n"
        f"- edition_id 默认：`{edition_id or '（自拟或沿用）'}`\n\n"
        "编排器已完成本轮三角裁决（**不要**再调用 layout.triad / judge / anchor，结果如下）：\n"
        f"```json\n{triad_json}\n```\n\n"
        "规则：\n"
        "- **35B（judge）= 主力**；**MinerU/结构锚点（anchor）= 辅助校正**；\n"
        "- 默认采信 `merged` / `page_plan_draft`；\n"
        f"- {escalate_block}"
        "- 每处理完分歧页或写完方案就 `session.update_todos` 打心跳；\n"
        "- 可微调相邻同类页合并，action 用 mineru / skip / reprocess:layout_content 等。\n\n"
        "本轮结束时**只**输出一个 ```json 块（不要最终报告，不要 start_import）：\n"
        "```json\n"
        "{\n"
        '  "pages_done": "' + f"{start}-{end}" + '",\n'
        '  "page_plan_partial": [\n'
        '    {"pages": "…", "category": "…", "edition_id": "…", "action": "mineru|skip|reprocess:…", "notes": "…"}\n'
        "  ],\n"
        '  "notes": "可选"\n'
        "}\n"
        "```\n"
        "禁止处理本轮以外的页；禁止开始导入。"
    )


def final_import_instruction(
    *,
    minio_key: str,
    page_count: int,
    page_plan: list[dict[str, Any]],
    page_limit: int,
    edition_id: str,
) -> str:
    plan_json = json.dumps(page_plan, ensure_ascii=False, indent=2)
    return (
        f"## 全书预览已完成（共 {page_count} 页均已三角裁决归类）\n"
        f"minio_key=`{minio_key}`\n"
        f"edition_id 默认 `{edition_id or '（见 page_plan）'}`\n\n"
        "已汇总的 page_plan：\n"
        f"```json\n{plan_json}\n```\n\n"
        "现在进入导入与验收（仍可按 page_plan 微调）：\n"
        f"1. `corpus.start_import`（page_limit={page_limit}，wait=true，预览版导入；"
        "工具会自动向用户明示「已导入」并打开导入面板）\n"
        "2. 抽查 page_context / page_html；偏差页按 page_plan 的 action 做 reprocess\n"
        "3. harness.run_golden；需要时 page_diff\n"
        "4. 输出最终报告 ```json，必须含：\n"
        '   status/summary/evidence/page_plan/**job_id**/**import_line**（\"mineru\"）\n'
        "   summary 里用中文写清「已预览版导入 job_id=…」。\n"
        "不要再重新全书三角裁决或读图。"
    )


def detect_page_limit_from_messages(messages: list[dict[str, Any]], default: int = 12) -> int:
    """从用户消息里抓「导入 N 页」；默认 12。"""
    pat = re.compile(r"(?:导入|page_limit\s*=?\s*|前)\s*(\d{1,3})\s*页")
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        text = str((m.get("content") or {}).get("text") or "")
        hit = pat.search(text)
        if hit:
            n = int(hit.group(1))
            if 1 <= n <= 500:
                return n
    return default


def user_wants_full_preview(messages: list[dict[str, Any]]) -> bool:
    """用户是否明确要求全书/全部预览。"""
    keys = ("全部预览", "全页预览", "每一页", "所有页", "全书预览", "全部的页面", "所有的页面")
    for m in messages:
        if m.get("role") != "user":
            continue
        text = str((m.get("content") or {}).get("text") or "")
        if any(k in text for k in keys):
            return True
    return False
