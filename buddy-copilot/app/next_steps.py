"""状态化下一步按钮：awaiting_user 停靠时编排器强制附带 actions。"""

from __future__ import annotations

from typing import Any

from .segment_state import (
    has_layout_or_effective_ledger,
    imported_through,
    open_issue_pages,
)


def _edition_target(payload: dict[str, Any]) -> str:
    """待审议落点：优先本轮 fork 结果，否则当前绑定方案。"""
    return str(
        payload.get("result_edition_id")
        or payload.get("edition_id")
        or payload.get("source_edition_id")
        or ""
    ).strip()


def build_next_step_actions(payload: dict[str, Any]) -> list[dict[str, str]]:
    """按会话 payload 生成推荐按钮（label → send）。至少返回「继续」。"""
    eid = _edition_target(payload)
    result = str(payload.get("result_edition_id") or "").strip()
    forked = bool(payload.get("result_edition_forked"))
    if result and forked:
        return [
            {
                "id": "open_edition",
                "label": "打开方案页",
                "send": f"__ui:open_edition:{result}",
            },
            {
                "id": "open_corrections",
                "label": "打开待审议",
                "send": f"__ui:open_corrections:{result}",
            },
            {"id": "done", "label": "结束", "send": "结束"},
        ]
    if result and not forked:
        # Associated draft exists but was not created this round — don't claim「新建」.
        return [
            {
                "id": "open_corrections",
                "label": "打开待审议",
                "send": f"__ui:open_corrections:{result}",
            },
            {
                "id": "open_edition",
                "label": "打开方案页",
                "send": f"__ui:open_edition:{result}",
            },
            {
                "id": "finalize",
                "label": "生成新排版方案",
                "send": "生成新排版方案",
            },
            {"id": "done", "label": "结束", "send": "结束"},
        ]

    pages = open_issue_pages(payload)
    if pages:
        first = pages[0]
        return [
            {"id": "accept", "label": "继续自动改", "send": "按建议改"},
            {
                "id": "peek",
                "label": f"先看第 {first} 页",
                "send": f"先看第 {first} 页",
            },
            {"id": "done", "label": "结束", "send": "结束"},
        ]

    pc = int(payload.get("page_count") or 0)
    through = imported_through(payload)
    if (
        payload.get("import_job_id")
        and has_layout_or_effective_ledger(payload)
        and not payload.get("result_edition_id")
        and (pc <= 0 or through >= pc or payload.get("fullbook_autofix_done"))
    ):
        return [
            {
                "id": "finalize",
                "label": "生成新排版方案",
                "send": "生成新排版方案",
            },
            {"id": "continue", "label": "继续", "send": "继续"},
        ]

    if eid and has_layout_or_effective_ledger(payload):
        return [
            {
                "id": "open_corrections",
                "label": "打开待审议",
                "send": f"__ui:open_corrections:{eid}",
            },
            {
                "id": "finalize",
                "label": "生成新排版方案",
                "send": "生成新排版方案",
            },
            {"id": "continue", "label": "继续", "send": "继续"},
        ]

    return [{"id": "continue", "label": "继续", "send": "继续"}]


def next_steps_status_text(payload: dict[str, Any]) -> str:
    """一句说清现状，配合 actions。文案必须与真实落点一致。"""
    n_log = len(payload.get("layout_change_log") or [])
    n_eff = len(payload.get("effective_changes") or [])
    result = str(payload.get("result_edition_id") or "").strip()
    forked = bool(payload.get("result_edition_forked"))
    pending_hint = ""
    if n_eff:
        pending_hint = f" 本轮已记入待审议 {n_eff} 条。"
    elif n_log:
        pending_hint = (
            f" 会话账本有 layout 改动 {n_log} 条；"
            "若未点「记入待审议」，方案页待审队列可能仍为空。"
        )

    if result and forked:
        return (
            f"已新建排版方案草稿 `{result}`。"
            f"{pending_hint}"
            "可打开方案页 / 待审议审阅，或结束本会话。"
        )
    if result and not forked:
        return (
            f"当前关联方案草稿 `{result}`（非本轮新建）。"
            f"{pending_hint}"
            "请到方案页核对「待审建议」；需要新 fork 可再说「生成新排版方案」。"
        )

    pages = open_issue_pages(payload)
    through = imported_through(payload)
    pc = int(payload.get("page_count") or 0)
    job = payload.get("import_job_id") or "?"
    if pages:
        return (
            f"任务 `{job}` 全书约已导入至第 {through or pc or '?'} 页；"
            f"仍有遗留页 {pages}（可继续自动改，或事后逐页说明）。"
        )
    if has_layout_or_effective_ledger(payload):
        return (
            f"全书导入已完成（job=`{job}`）。"
            f"账本：layout_change_log={n_log} · effective_changes={n_eff}。"
            "可生成新排版方案草稿，或先打开当前方案的待审议。"
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
