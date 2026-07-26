"""Agent 契约与 HTTP API 模型。

v2（Cursor 式自主执行）：Agent 不再产出 Plan JSON 由编排器代执行，
而是一次运行内自主调 MCP 工具，收尾交「最终报告 JSON」。
本模块定义最终报告 schema 与工具参数的占位符校验。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- 最终报告（Agent 收尾契约，见 rules/00-copilot-core.md） ----


class PagePlanEntry(BaseModel):
    """页级排版方案条目：一段页码 → 版式类别 + 处理路径。"""

    pages: str  # 页码段，如 "1-4" / "5" / "6,8-10"
    category: str = ""  # 版式类别：目录 / 正文双栏 / 正文单栏+侧栏 / 习题 / 图版 / 公式密集…
    edition_id: str = ""  # 该页段应采用的方案（现有或 fork 出的新方案）
    action: str = ""  # 处理路径：mineru 默认 / reprocess:<mode> / skip / 35b…
    notes: str = ""  # 备注（如需要人工复核的原因）


class FinalReport(BaseModel):
    status: Literal["done", "blocked", "failed"]
    summary: str
    evidence: list[str] = Field(default_factory=list)
    next_steps: str = ""
    question: str = ""
    # 页级排版方案（import_advice 场景必填）：程序按此对每页分类归档、分流处理
    page_plan: list[PagePlanEntry] = Field(default_factory=list)
    # 若已发起导入：显式带回 job，前端结果卡可一键打开导入面板
    job_id: str = ""
    import_line: str = ""  # mineru | 35b


# ---- 占位符校验（工具参数纪律） ----

# 整串形如 <job_id>、{job_id}，或包含中文占位词 / TODO
_FULL_ANGLE = re.compile(r"^\s*<[^<>]{1,60}>\s*$")
_FULL_BRACE = re.compile(r"^\s*\{[A-Za-z_][\w-]{0,60}\}\s*$")
_PLACEHOLDER_WORDS = re.compile(r"待填|待上传|待定|待确认|占位|TODO|FIXME|placeholder", re.I)


def _is_placeholder(s: str) -> bool:
    return bool(_FULL_ANGLE.match(s) or _FULL_BRACE.match(s) or _PLACEHOLDER_WORDS.search(s))


def find_placeholders(obj: Any, path: str = "") -> list[str]:
    """递归扫描工具参数里的占位符，返回 `路径=值` 列表（空 = 干净）。"""
    hits: list[str] = []
    if isinstance(obj, str):
        if _is_placeholder(obj):
            hits.append(f"{path or '$'}={obj[:80]}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            hits.extend(find_placeholders(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(find_placeholders(v, f"{path}[{i}]"))
    return hits


# ---- HTTP API 模型 ----


class CreateSessionRequest(BaseModel):
    kind: Literal["import_advice", "page_assist"]
    payload: dict[str, Any] = Field(default_factory=dict)
    # 空 / "auto" = 自动（服务端选）；否则须为 /models 列表中的模型 id
    model: str = ""


class UserMessageRequest(BaseModel):
    text: str


class ApprovalDecisionRequest(BaseModel):
    approval_id: str
    approved: bool
    note: str = ""
    # 批准并「本会话总是允许」同一动作（会话级 override，不跨会话）
    remember: bool = False


class SessionOut(BaseModel):
    id: str
    kind: str
    status: str
    created_at: float
    updated_at: float
