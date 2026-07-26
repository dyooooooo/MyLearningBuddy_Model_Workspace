"""状态化下一步按钮：awaiting_user 停靠时编排器强制附带 actions。"""

from __future__ import annotations

from typing import Any

from .segment_state import (
    has_blocking_open_issues,
    has_layout_or_effective_ledger,
    imported_through,
    open_issue_pages,
)


def build_next_step_actions(payload: dict[str, Any]) -> list[dict[str, str]]:
    """按会话 payload 生成推荐按钮（label → send）。至少返回「继续」。"""
    if payload.get("result_edition_id"):
        eid = str(payload["result_edition_id"])
        return [
            {"id": "open_edition", "label": "打开方案页", "send": f"打开方案 {eid}"},
            {"id": "done", "label": "结束", "send": "结束"},
        ]

    pages = open_issue_pages(payload)
    if pages:
        first = pages[0]
        return [
            {"id": "accept", "label": "按建议改全部", "send": "按建议改"},
            {
                "id": "skip",
                "label": "不修，下一批",
                "send": "不修，下一批",
            },
            {
                "id": "peek",
                "label": f"先看第 {first} 页",
                "send": f"先看第 {first} 页",
            },
        ]

    nxt = int(payload.get("segment_next_page") or 0)
    pc = int(payload.get("page_count") or 0)
    through = imported_through(payload)
    if 0 < nxt <= pc:
        return [
            {
                "id": "next_batch",
                "label": "导入下一批",
                "send": "继续",
            },
            {
                "id": "confirm",
                "label": "先确认刚才改动有效",
                "send": "确认有效",
            },
        ]

    if (
        (nxt > pc or (pc > 0 and through >= pc))
        and payload.get("import_job_id")
        and has_layout_or_effective_ledger(payload)
        and not payload.get("result_edition_id")
    ):
        return [
            {
                "id": "finalize",
                "label": "生成新排版方案",
                "send": "生成新排版方案",
            },
            {"id": "continue", "label": "继续", "send": "继续"},
        ]

    return [{"id": "continue", "label": "继续", "send": "继续"}]


def next_steps_status_text(payload: dict[str, Any]) -> str:
    """一句说清现状，配合 actions。"""
    if payload.get("result_edition_id"):
        return (
            f"已形成新排版方案草稿 `{payload['result_edition_id']}`。"
            "可打开方案页审阅，或结束本会话。"
        )
    pages = open_issue_pages(payload)
    through = imported_through(payload)
    nxt = int(payload.get("segment_next_page") or 0)
    pc = int(payload.get("page_count") or 0)
    job = payload.get("import_job_id") or "?"
    if pages:
        return (
            f"任务 `{job}` 已导入至约第 {through or '?'} 页；"
            f"AI 已根据最终页面列出问题页 {pages}。"
            "请选择：不修进下一批、按建议改全部，或直接说「修第 X 页」。"
        )
    if 0 < nxt <= pc:
        return (
            f"任务 `{job}` 已导入至第 {through or nxt - 1} 页；"
            f"下一批从第 {nxt} 页开始。可点「导入下一批」，或先确认改动有效。"
        )
    if has_layout_or_effective_ledger(payload) and not payload.get("result_edition_id"):
        n_log = len(payload.get("layout_change_log") or [])
        n_eff = len(payload.get("effective_changes") or [])
        return (
            f"全书分段已完成（job=`{job}`）。"
            f"账本：layout_change_log={n_log} · effective_changes={n_eff}。"
            "可生成新排版方案草稿。"
        )
    return f"可点「继续」或直接说明下一步（job=`{job}`）。"


def next_steps_message(payload: dict[str, Any], *, preface: str = "") -> dict[str, Any]:
    text = (preface.strip() + "\n" if preface.strip() else "") + next_steps_status_text(
        payload
    )
    return {"text": text.strip(), "actions": build_next_step_actions(payload)}


def looks_like_finalize_request(text: str) -> bool:
    t = (text or "").strip()
    return bool(
        t
        and (
            "生成新排版" in t
            or "生成新方案" in t
            or "fork" in t.lower()
            or t in ("finalize", "沉淀方案", "形成新方案")
        )
    )


def looks_like_refuse_principle(text: str) -> bool:
    """粗检：用户是否在要求越权（编排器层快速拒绝，不全靠模型）。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    needles = (
        "改架构",
        "删权限",
        "import35b",
        "动 35b",
        "动35b",
        "改 docker",
        "改permissions",
        "跳过权限",
    )
    return any(n in t for n in needles)
