"""Heuristic triage: T1 findings + user text → suggested fix layer and files."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TriageResult:
    layer: str
    confidence: str
    rationale: str
    suggested_files: list[str] = field(default_factory=list)
    plan_mode: str = "execute_direct"
    entry_doc: str = "EXEC.plan.md"


_LAYER_FILES: dict[str, list[str]] = {
    "boundary": [
        "learning-buddy-api/app/import_35b/model_boundary.py",
    ],
    "compiler": [
        "learning-buddy-api/app/import_35b/page_compiler.py",
        "learning-buddy-api/app/import_35b/render_zone.py",
    ],
    "prompt": [
        "learning-buddy-api/app/import_35b/prompts/layout_blueprint_v6.md",
        "learning-buddy-api/app/import_35b/schemas/llm_view.py",
    ],
    "layout": [
        "learning-buddy-api/app/import_35b/mineru_layout.py",
        "learning-buddy-api/app/import_35b/vision_layout.py",
        "learning-buddy-api/app/import_35b/prompts/layout_blueprint_v6.md",
    ],
    "normalize": [
        "learning-buddy-api/app/import_35b/normalize.py",
    ],
    "render": [
        "learning-buddy-api/app/import_35b/ssr_math.py",
        "exam-ocr-server/studio-ui/src/util/math-render.ts",
    ],
    "harness": [
        "learning-buddy-api/app/import_35b/validation/rules.py",
        "learning-buddy-api/data/eval/",
    ],
}

_LAYOUT_SUSPECTS = frozenset(
    {"ASIDE_IN_MAIN", "DUPLICATE_FIGURE_BBOX", "TOC_AS_TABLE"}
)


def _suspect_types(t1: list[dict[str, Any]]) -> set[str]:
    return {str(f.get("suspect_type") or "") for f in t1 if isinstance(f, dict)}


def triage_feedback(
    *,
    t1_snapshot: list[dict[str, Any]],
    user_body: str,
    tags: list[str] | None = None,
) -> TriageResult:
    types = _suspect_types(t1_snapshot)
    body = (user_body or "").strip()
    tag_set = set(tags or [])

    if "layout" in tag_set or types & _LAYOUT_SUSPECTS:
        hit = types & _LAYOUT_SUSPECTS
        return TriageResult(
            layer="layout",
            confidence="high" if hit else "medium",
            rationale=(
                f"layout 标签或 T1 {hit or '排版反馈'}：优先 PageReprocess/BlockRefine，"
                "再查 MinerU hints / v6 prompt / profile features"
            ),
            suggested_files=_LAYER_FILES["layout"],
        )
    if "MAIN_EMPTY" in types:
        return TriageResult(
            layer="compiler",
            confidence="high",
            rationale="T1 MAIN_EMPTY：有 main 块但 lb-main 为空，优先查编译/role-zone 路由",
            suggested_files=_LAYER_FILES["compiler"],
        )
    if types & {"CONTROL_CHAR_RESIDUE", "ENV_UNBALANCED"}:
        return TriageResult(
            layer="boundary",
            confidence="high",
            rationale=f"T1 检出 {types & {'CONTROL_CHAR_RESIDUE', 'ENV_UNBALANCED'}}，优先扩防腐层白名单",
            suggested_files=_LAYER_FILES["boundary"],
        )
    if "ROLE_ZONE_MISMATCH" in types:
        return TriageResult(
            layer="prompt",
            confidence="medium",
            rationale="T1 ROLE_ZONE_MISMATCH：role/zone 标注与编译路由不一致",
            suggested_files=_LAYER_FILES["prompt"],
        )
    if types & {"MATH_FALLBACK", "MATH_RENDER_FAILED", "SSR_INVALID", "SSR_MISSING", "SSR_UNAVAILABLE"}:
        return TriageResult(
            layer="render",
            confidence="medium",
            rationale=f"T1 渲染类 {types & {'MATH_FALLBACK', 'MATH_RENDER_FAILED', 'SSR_INVALID', 'SSR_MISSING', 'SSR_UNAVAILABLE'}}",
            suggested_files=_LAYER_FILES["render"],
        )
    if "blank_page" in tag_set or "空白" in body:
        return TriageResult(
            layer="compiler",
            confidence="medium",
            rationale="用户标记空白页，结合 blueprint/HTML 查 main 路由",
            suggested_files=_LAYER_FILES["compiler"],
        )
    if body and not t1_snapshot:
        return TriageResult(
            layer="harness",
            confidence="low",
            rationale="用户有描述但 T1 未检出，可能需补规则或 prompt；建议 Plan 模式先细化",
            suggested_files=_LAYER_FILES["harness"],
            plan_mode="refine_first",
            entry_doc="PLAN.prompt.md",
        )
    if body:
        return TriageResult(
            layer="compiler",
            confidence="low",
            rationale="有用户描述，默认从编译/HTML 对照查起",
            suggested_files=_LAYER_FILES["compiler"],
            plan_mode="refine_first",
            entry_doc="PLAN.prompt.md",
        )
    return TriageResult(
        layer="harness",
        confidence="low",
        rationale="信息不足，需结合截图与 evidence 人工判断",
        suggested_files=_LAYER_FILES["harness"],
        plan_mode="refine_first",
        entry_doc="PLAN.prompt.md",
    )
