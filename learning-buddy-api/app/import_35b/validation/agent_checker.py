"""T2 Agent 视觉比对校验（异步面，仅产疑点）。

架构定位（文档 4.0 公理 3「LLM/Agent 只出现在怀疑/提议位置」）：
- 输入：本页原图 + 编译产物的纯文本。让 35B VLM 找「原图里有、但提取文本里缺失
  或明显错乱」的内容——**只产 finding（疑点），永不改数据**，与 T1 同一数据模型
  （``detector="agent"``），落 HITL 审核队列由人裁决。
- 默认关闭（``settings.import35b_agent_check_enabled``，激活门槛见文档 4.5）；
  关闭或 VLM 不可用时返回 ``[]``，绝不阻断导入主流程。
- 有界：``max_tokens`` 上限 + guided JSON；解析失败即空返回。

隔离：仅复用 35B 线自己的 vLLM 客户端（``app.clients.vllm``，与生成面同一后端），
不新增服务，不触碰 MinerU 线。
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.import_35b.render_zone import html_to_plain_text
from app.import_35b.validation.rules import Finding

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是教材数字化的质检员。给你一页教材原图，以及系统从这页提取的纯文本。\n"
    "你的唯一任务：找出原图中清晰可见、但提取文本里【缺失】或【明显错乱】的内容。\n"
    "严格规则：\n"
    "1. 只报告缺失或错乱，不要报告排版/字体差异；不确定的不要报告。\n"
    "2. 公式渲染成 SVG 属正常，不要因为文本里看不到公式符号就报告。\n"
    "3. 输出 JSON：{\"issues\":[{\"type\":\"missing|garbled\",\"excerpt\":\"原图中的该内容（≤40字）\",\"reason\":\"简述\"}]}\n"
    "4. 没有问题时输出 {\"issues\":[]}。"
)

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["missing", "garbled"]},
                    "excerpt": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["type", "excerpt"],
            },
        }
    },
    "required": ["issues"],
}


def agent_check_enabled() -> bool:
    return bool(settings.import35b_agent_check_enabled)


def run_agent_checks(
    blueprint: Any,
    html: str,
    *,
    page_jpeg: bytes | None,
    subject: str = "math",
    grade_band: str = "hs",
) -> list[Finding]:
    """VLM 视觉比对，产 ``detector="agent"`` 的疑点 finding。默认关闭时返回 []。"""
    del subject, grade_band  # reserved for future per-subject prompt tuning
    if not agent_check_enabled() or not page_jpeg:
        return []

    plain = (html_to_plain_text(html) or "").strip()
    page_no = int(getattr(blueprint, "page_no", 0) or 0)
    user_text = (
        "系统提取的纯文本如下（三引号内）：\n\"\"\"\n"
        + plain[:6000]
        + "\n\"\"\"\n请对照原图，按规则输出缺失/错乱清单 JSON。"
    )

    try:
        from app.clients.vllm import vision_json_completion

        raw = vision_json_completion(
            page_jpeg,
            user_text,
            system=_SYSTEM,
            max_tokens=settings.import35b_agent_check_max_tokens,
            response_schema=_RESPONSE_SCHEMA,
            schema_name="agent_visual_diff",
        )
    except Exception as exc:  # noqa: BLE001 — Agent 校验绝不阻断导入
        logger.warning("agent_check page %d failed: %s", page_no, exc)
        return []

    issues = raw.get("issues") if isinstance(raw, dict) else None
    if not isinstance(issues, list):
        return []

    findings: list[Finding] = []
    for it in issues[:20]:
        if not isinstance(it, dict):
            continue
        excerpt = str(it.get("excerpt") or "").strip()
        if not excerpt:
            continue
        itype = str(it.get("type") or "missing")
        reason = str(it.get("reason") or "")[:200]
        findings.append(
            Finding(
                suspect_type="AGENT_MISSING" if itype == "missing" else "AGENT_GARBLED",
                severity="medium",
                page_no=page_no,
                evidence={"excerpt": excerpt[:120], "reason": reason},
                candidates=[
                    "原图确有此内容但被漏抽/错乱（需补录或修正）",
                    "Agent 误报（图中并无此内容或已正确呈现）",
                ],
                detector="agent",
            ).finalize()
        )
    return findings
