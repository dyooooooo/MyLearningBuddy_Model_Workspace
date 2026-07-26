"""T1 确定性规则校验：编译产物 + blueprint 的页级契约不变量。

每条 finding 是「疑点」不是「结论」：带证据、候选诊断（给用户做选择题）
和优先级，落 ``import35b_review_items`` 走 HITL 确认。规则只做确定性
判断，绝不猜测语义——猜测是 T2 Agent（预留）和人的事。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.import_35b.model_boundary import has_hard_control_chars
from app.import_35b.payload_heuristics import (
    duplicate_figure_bboxes,
    figure_caption_should_demote,
    should_reroute_aside_left,
    table_looks_like_toc,
)
from app.import_35b.schemas import PageBlueprint

Severity = Literal["high", "medium", "low"]


class Finding(BaseModel):
    suspect_type: str
    severity: Severity = "medium"
    page_no: int
    block_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    # 候选诊断：面板上作为选择题选项呈现，用户没猜中可自由填写
    candidates: list[str] = Field(default_factory=list)
    # 指纹：不含 page_no 的内容指纹，同类问题跨页/跨 job 可被同一条抑制命中
    fingerprint: str = ""
    # 精确指纹：含 page_no，「仅此处」抑制用
    fingerprint_exact: str = ""
    # 探测器来源：rule（T1 确定性）| agent（T2 视觉比对）| user_report
    detector: str = "rule"

    def finalize(self) -> "Finding":
        core = f"{self.suspect_type}|{self.block_id}|{_norm_evidence(self.evidence)}"
        self.fingerprint = hashlib.sha1(core.encode("utf-8")).hexdigest()[:16]
        self.fingerprint_exact = hashlib.sha1(
            f"{core}|p{self.page_no}".encode("utf-8")
        ).hexdigest()[:16]
        return self


def _norm_evidence(evidence: dict[str, Any]) -> str:
    parts = [f"{k}={str(v)[:120]}" for k, v in sorted(evidence.items())]
    return ";".join(parts)


_FALLBACK_SPAN_RE = re.compile(r'<span class="math math--fallback">([^<]*)</span>')
_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")

_ASIDE_CAPTION_MAX = 80


def _block_strings(block: Any) -> list[str]:
    out: list[str] = []
    content = block.content
    out.extend(content.text_paragraphs())
    if content.text_segments:
        for seg in content.text_segments:
            out.append(str(seg.get("raw") or ""))
            out.append(str(seg.get("latex") or ""))
    eq = content.equation_latex()
    if eq:
        out.append(eq)
    cap = content.figure_caption() or content.table_caption()
    if cap:
        out.append(cap)
    for row in content.table_rows():
        out.extend(str(c) for c in row)
    return [s for s in out if s]


def run_page_checks(
    blueprint: PageBlueprint,
    html: str,
    *,
    ssr_report: list[dict[str, Any]] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    page_no = blueprint.page_no

    # 只统计会路由进 main 的块（未知 zone 也归 main，与编译器一致）
    main_blocks = [
        b
        for b in blueprint.blocks
        if b.zone not in ("running", "aside_left", "aside_right", "full_bleed")
        and b.kind != "running"
    ]

    # ── 1. main 覆盖率：页面有 main 块但 main 区为空（p3 吞块类）─────────
    main_match = re.search(r'<section class="lb-main"[^>]*>(.*?)</section>', html or "", re.S)
    main_inner = (main_match.group(1) if main_match else "").strip()
    if main_blocks and not main_inner:
        findings.append(
            Finding(
                suspect_type="MAIN_EMPTY",
                severity="high",
                page_no=page_no,
                evidence={"main_blocks": len(main_blocks)},
                candidates=[
                    "整页内容丢失（模型误标 role/zone）",
                    "本页本来就是空白页/纯图页",
                ],
            ).finalize()
        )

    for block in blueprint.blocks:
        strings = _block_strings(block)

        # ── 2. role/zone 不一致：role=running_header 但 zone 不是 running ──
        if block.role == "running_header" and block.zone != "running" and block.kind != "running":
            head = (strings[0] if strings else "")[:60]
            findings.append(
                Finding(
                    suspect_type="ROLE_ZONE_MISMATCH",
                    severity="medium",
                    page_no=page_no,
                    block_id=block.id,
                    evidence={"role": block.role, "zone": block.zone, "text_head": head},
                    candidates=[
                        "是正文内容（当前已渲染在正文，正确）",
                        "确实是页眉（应从正文移除）",
                    ],
                ).finalize()
            )

        # ── 3. 控制字符残留：防腐层白名单没接住的损伤 ──────────────────
        for s in strings:
            if has_hard_control_chars(s):
                findings.append(
                    Finding(
                        suspect_type="CONTROL_CHAR_RESIDUE",
                        severity="high",
                        page_no=page_no,
                        block_id=block.id,
                        evidence={"text": s[:120]},
                        candidates=[
                            "公式被 JSON 转义破坏（需补白名单规则）",
                            "原文即含特殊字符",
                        ],
                    ).finalize()
                )
                break

        # ── 4. 环境配对：自动闭合后仍不平衡（跨字段截断等）──────────────
        for s in strings:
            begins = _BEGIN_RE.findall(s)
            ends = _END_RE.findall(s)
            if sorted(begins) != sorted(ends):
                findings.append(
                    Finding(
                        suspect_type="ENV_UNBALANCED",
                        severity="high",
                        page_no=page_no,
                        block_id=block.id,
                        evidence={"begins": begins, "ends": ends, "text": s[:120]},
                        candidates=["公式环境被截断", "原文如此"],
                    ).finalize()
                )
                break

        # ── 5. 侧栏 figure caption 超长：正文被塞进配图说明（p24 类）────
        if (
            block.kind == "figure"
            and block.zone in ("aside_left", "aside_right")
            and len(block.content.figure_caption() or "") > _ASIDE_CAPTION_MAX
        ):
            findings.append(
                Finding(
                    suspect_type="ASIDE_CAPTION_OVERLONG",
                    severity="medium",
                    page_no=page_no,
                    block_id=block.id,
                    evidence={"caption_len": len(block.content.figure_caption() or "")},
                    candidates=[
                        "caption 里混入了正文（应拆成 callout 文本块）",
                        "配图说明确实这么长",
                    ],
                ).finalize()
            )

    # ── 6. 假 figure：正文/目录 caption（含已裁 asset）──────────────────
    dup_ids = duplicate_figure_bboxes(list(blueprint.blocks))
    for block in blueprint.blocks:
        if block.kind == "figure":
            cap = block.content.figure_caption() or ""
            if figure_caption_should_demote(cap):
                findings.append(
                    Finding(
                        suspect_type="FIGURE_BODY_CAPTION",
                        severity="medium",
                        page_no=page_no,
                        block_id=block.id,
                        evidence={
                            "caption": cap[:120],
                            "has_asset": bool(block.content.asset_url),
                        },
                        candidates=[
                            "目录/正文被误标为 figure（应改为 text）",
                            "确为插图且 caption 异常",
                        ],
                    ).finalize()
                )
            if block.id in dup_ids:
                findings.append(
                    Finding(
                        suspect_type="DUPLICATE_FIGURE_BBOX",
                        severity="medium",
                        page_no=page_no,
                        block_id=block.id,
                        evidence={"bbox": list(block.bbox or [])},
                        candidates=[
                            "多块共用同一 bbox（目录行误标）",
                            "同图多块合法",
                        ],
                    ).finalize()
                )

        # ── 7. TOC 误标 table ──────────────────────────────────────────
        if (
            block.kind == "table"
            and block.role != "toc"
            and table_looks_like_toc(block.content.table_rows())
        ):
            findings.append(
                Finding(
                    suspect_type="TOC_AS_TABLE",
                    severity="medium",
                    page_no=page_no,
                    block_id=block.id,
                    evidence={"rows": len(block.content.table_rows())},
                    candidates=[
                        "目录被标成 table（应 text/toc）",
                        "确为数据表",
                    ],
                ).finalize()
            )

        # ── 8. 左栏批注仍在 main（normalize 未改写时）──────────────────
        if block.zone == "main" and should_reroute_aside_left(
            list(block.bbox or []), zone=block.zone
        ):
            findings.append(
                Finding(
                    suspect_type="ASIDE_IN_MAIN",
                    severity="low",
                    page_no=page_no,
                    block_id=block.id,
                    evidence={"bbox": list(block.bbox or []), "role": block.role},
                    candidates=[
                        "左侧批注应放 aside_left",
                        "确为正文窄列",
                    ],
                ).finalize()
            )

    # ── 9. 公式降级：math--fallback 出现即说明有表达式没能进 SSR ────────
    fallbacks = _FALLBACK_SPAN_RE.findall(html or "")
    for fb in dict.fromkeys(fallbacks):
        findings.append(
            Finding(
                suspect_type="MATH_FALLBACK",
                severity="low",
                page_no=page_no,
                evidence={"text": fb[:120]},
                candidates=[
                    "公式识别错误（请填写正确 LaTeX）",
                    "降级显示可接受",
                ],
            ).finalize()
        )

    # ── 10. SSR 渲染失败：编译面上报的可见 error 标记 ────────────────────
    for item in ssr_report or []:
        findings.append(
            Finding(
                suspect_type=str(item.get("suspect_type") or "MATH_RENDER_FAILED"),
                severity="high",
                page_no=page_no,
                evidence={"latex": str(item.get("latex") or "")[:200]},
                candidates=[
                    "LaTeX 有误（请填写正确 LaTeX）",
                    "渲染器缺包（系统问题）",
                ],
            ).finalize()
        )

    return findings
